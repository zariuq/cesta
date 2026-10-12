"""Independent compiler witnesses and explicit dispositions for oracle differences."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from corpus import ROOT, pycparser_check, run


def normalized(message):
    return message.translate(str.maketrans({"‘":"'", "’":"'", "“":'"', "”":'"'}))


def errors(result):
    return [normalized(item) for item in re.findall(r'error: ([^\n]+)', result["diagnostic"])]


# These are compiler constraint diagnostics, not a second implementation of C syntax.
CONSTRAINTS = {
    "type-specifier-combination": [r"two or more data types", r"duplicate '(?:typedef|unsigned|signed|short)'",
        r"'long long long' is too long", r"cannot combine with previous"],
    "return-value": [r"'return' with no value", r"non-void function .* should return a value"],
    "name-resolution": [r"'.*' undeclared", r"use of undeclared identifier", r"unknown type name",
        r"must use 'struct' tag"],
    "type-redeclaration": [r"conflicting type qualifiers", r"conflicting types", r"typedef redefinition",
        r"'.*' redeclared as different kind", r"redefinition of '.*' as different kind"],
    "duplicate-member-or-parameter": [r"duplicate member", r"redefinition of parameter"],
    "member-access": [r"'.*' has no member named", r"no member named", r"invalid type argument of '->'",
        r"member reference type .* is not a pointer", r"request for member .* not a structure or union"],
    "operand-type": [r"invalid type argument of unary", r"indirection requires pointer operand",
        r"invalid operands to binary", r"expression is not assignable"],
    "incomplete-type": [r"parameter .* has incomplete type", r"storage size of .* isn't known",
        r"variable has incomplete type", r"tentative definition has type .* never completed"],
    "initializer-type": [r"function .* is initialized like a variable", r"illegal initializer",
        r"initialization of flexible array", r"designator into flexible array", r"flexible array requires"],
    "function-declarator-type": [r".*declared as function returning a function", r"function cannot return function type"],
    "parameter-list-constraint": [r"parameter names \(without types\) in function declaration",
        r"a parameter list without types is only allowed in a function definition",
        r"'void' must be the only parameter and unnamed", r"'void' must be the first and only parameter",
        r"argument may not have 'void' type"],
}


def constraint_family(message):
    for family, patterns in CONSTRAINTS.items():
        if any(re.match(pattern, message) for pattern in patterns):
            return family
    return None


def ordinary_function_body(wire):
    """Read an actual exported declaration shape, including its direct children."""
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', wire.decode())
    position = 0
    def term():
        nonlocal position
        token = tokens[position]; position += 1
        if token != "(":
            return token
        items = []
        while tokens[position] != ")":
            items.append(term())
        position += 1
        return items
    tree = term()
    if position != len(tokens):
        raise RuntimeError("unexpected trailing exported tree")
    def walk(node):
        if not isinstance(node,list):
            return False
        if node and node[0] == "c:FunctionDefinition":
            declarator = node[2]
            if (declarator[0] == "c:Declarator" and declarator[1][0] == "c:NameDeclarator"):
                return True
        return any(walk(child) for child in node)
    return walk(tree)


def native_disposition(record, path):
    native = record["cesta"]["accepted"]
    families, unknown = set(), []
    mismatched = [name for name in ("gcc","clang") if record[name]["accepted"] != native]
    if not mismatched:
        return [], []
    if not native:
        # Strict C11 oracle checks distinguish recovery/extensions from valid grammar.
        strict = {name:run([name,"-std=c11","-pedantic-errors","-fsyntax-only",str(path)])[0]
                  for name in ("gcc","clang")}
        record["strict_c11"] = strict
        if all(not result["accepted"] for result in strict.values()):
            messages = normalized(record["gcc"]["diagnostic"]+record["clang"]["diagnostic"])
            if "label at end of compound statement" in messages:
                families.add("dialect:C23-trailing-label")
            elif "no semicolon at end of struct or union" in messages or "expected ';' at end of declaration list" in messages:
                families.add("compiler-recovery:missing-member-semicolon")
            elif "empty declaration" in messages and "useless type qualifier" in messages:
                families.add("declared-constraint:missing-type-specifier-6.7.2p2")
            else:
                unknown.append("strict C11 rejection lacks a reviewed recovery/profile cause")
        else:
            unknown.append("native reader rejects source accepted by a strict C11 compiler")
    else:
        function_shape = None
        for name in mismatched:
            messages = errors(record[name])
            if not messages:
                unknown.append(f"{name} failed without a classified error")
            for message in messages:
                family = constraint_family(message)
                if family:
                    families.add("constraint:"+family)
                elif (message.startswith("expected '=','") or
                      (message.startswith("expected '") and "before '{' token" in message) or
                      message == "expected ';' after top level declarator"):
                    if function_shape is None:
                        result, wire = run([str(ROOT / "build/cesta"),"read","--std=c11",str(path)])
                        function_shape = result["accepted"] and ordinary_function_body(wire)
                    if function_shape:
                        families.add("constraint:nonfunction-definition-declarator-6.9.1p2")
                    else:
                        unknown.append(name+": "+message)
                else:
                    unknown.append(name+": "+message)
    return sorted(families), unknown


def load_pyc_audit():
    return json.loads((ROOT / "tests/data/pycparser_mutant_audit.json").read_text())


def mutant_disposition(record, path, audit):
    families, unknown = native_disposition(record,path)
    statuses = [record[name]["accepted"] for name in ("cesta","gcc","clang")]
    pyc = record["pycparser"]
    discrepancy = not pyc["valid_result"] or pyc["accepted"] not in set(statuses)
    # If the native reader admits a constraint-invalid tree, a compiler refusal
    # already establishes the stage distinction, independently of pycparser.
    needs_pyc_cause = not pyc["valid_result"] or (len(set(statuses)) == 1 and discrepancy)
    if needs_pyc_cause:
        case = audit["cases"].get(record["sha256"])
        if case and all(pyc.get(key) == value for key,value in case["pycparser"].items()):
            families.append("pycparser:"+case["family"])
        elif (statuses == [True,True,True] and record["source"] == "designator.c" and
              record["edit"] == "delete" and record["spelling_hex"] == "3d" and
              pyc["valid_result"] and not pyc["accepted"] and
              "GNU 'missing =' extension in designator" in record["clang"]["diagnostic"]):
            families.append("pycparser:GNU-old-array-initializer")
        else:
            unknown.append("pycparser discrepancy is outside the source-hashed audit")
    if len(set(statuses)) > 1 and not families:
        unknown.append("compiler/native acceptance difference lacks a disposition")
    return sorted(set(families)), unknown


WITNESSES = {
    "type-specifier-combination": ("unsigned unsigned int x;", "unsigned int x;"),
    "return-value": ("int f(void){return;}", "int f(void){return 0;}"),
    "name-resolution": ("int f(void){return x;}", "int x; int f(void){return x;}"),
    "type-redeclaration": ("typedef int T; typedef const int T;", "typedef int T; typedef int T;"),
    "duplicate-member-or-parameter": ("struct S{int x; int x;};", "struct S{int x; int y;};"),
    "member-access": ("struct S{int x;}; int f(struct S s){return s.y;}", "struct S{int x;}; int f(struct S s){return s.x;}"),
    "operand-type": ("int f(int x){return *x;}", "int f(int*x){return *x;}"),
    "incomplete-type": ("struct S; struct S x;", "struct S{int x;}; struct S x;"),
    "initializer-type": ("int f(void)=1;", "int x=1;"),
    "function-declarator-type": ("int f(void)(int);", "int(*f(void))(int);"),
    "parameter-list-constraint": ("int f(void,int);", "int f(int);"),
    "nonfunction-definition-declarator-6.9.1p2": ("int x {return 0;}", "int x(void){return 0;}"),
}


def check_controls(probe, output, audit):
    output.mkdir(parents=True,exist_ok=True)
    path = output / "current.c"
    records = []
    def measure(source):
        path.write_text(source+"\n")
        record = {"source":source,"cesta":probe.accepts(path),"pycparser":pycparser_check(path.read_bytes())}
        for name in ("gcc","clang"):
            record[name] = run([name,"-std=c11","-fsyntax-only",str(path)])[0]
        return record
    for family,pair in WITNESSES.items():
        negative,positive = [measure(source) for source in pair]
        if (not negative["cesta"]["accepted"] or negative["gcc"]["accepted"] or
            not all(positive[name]["accepted"] for name in ("cesta","gcc","clang"))):
            raise RuntimeError(f"independent constraint witness changed: {family}: {negative}")
        # Recreate the negative source for the independently exported shape check.
        path.write_text(pair[0]+"\n")
        families,unknown = native_disposition(negative,path)
        if unknown or "constraint:"+family not in families:
            raise RuntimeError(f"constraint witness disposition changed: {family}: {unknown}: {families}")
        records.append({"family":family,"negative":negative,"positive":positive})
    for family,spec in audit["families"].items():
        pair = []
        for role in ("witness","control"):
            item = spec[role]
            record = measure(item["source"])
            if any(record[name].get(key) != value for name,expected in item["expected"].items()
                   for key,value in expected.items()):
                raise RuntimeError(f"pycparser witness/control changed: {family}/{role}: {record}")
            pair.append(record)
        records.append({"family":"pycparser:"+family,"negative":pair[0],"positive":pair[1]})
    recovery = {
        "compiler-recovery:missing-member-semicolon":("struct S{int x};","struct S{int x;};"),
        "dialect:C23-trailing-label":("void f(void){L:}","void f(void){L:;}"),
        "declared-constraint:missing-type-specifier-6.7.2p2":("const;","const int x;"),
    }
    for family,pair in recovery.items():
        negative,positive = [measure(source) for source in pair]
        path.write_text(pair[0]+"\n")
        families,unknown = native_disposition(negative,path)
        if unknown or family not in families or not all(positive[name]["accepted"] for name in ("cesta","gcc","clang")):
            raise RuntimeError(f"recovery/profile witness changed: {family}: {unknown}: {negative}")
        records.append({"family":family,"negative":negative,"positive":positive})
    old = measure("int a[8]={[5]7};")
    modern = measure("int a[8]={[5]=7};")
    if (not all(old[name]["accepted"] for name in ("cesta","gcc","clang")) or
        old["pycparser"]["accepted"] or not all(modern[name]["accepted"] for name in ("cesta","gcc","clang","pycparser"))):
        raise RuntimeError("GNU old-array-initializer oracle witness changed")
    records.append({"family":"pycparser:GNU-old-array-initializer","negative":old,"positive":modern})
    (output / "summary.json").write_text(json.dumps(records,indent=2)+"\n")
    return len(records)


def reconstruct(record, baselines, corpus):
    source = corpus / record["source"]
    result,data = run(["gcc","-std=c11","-E","-P",str(source)])
    if not result["accepted"] or hashlib.sha256(data).hexdigest() != baselines[record["source"]]:
        raise RuntimeError("audited mutant baseline changed")
    spelling = bytes.fromhex(record["spelling_hex"])
    offset = record["offset"]
    if data[offset:offset+len(spelling)] != spelling:
        raise RuntimeError("audited mutation token changed")
    replacement = b" "+spelling+b" "+spelling+b" " if record["edit"] == "duplicate" else b" "
    mutant = data[:offset]+replacement+data[offset+len(spelling):]
    if hashlib.sha256(mutant).hexdigest() != record["sha256"]:
        raise RuntimeError("audited mutant reconstruction differs")
    return mutant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summary",type=Path)
    parser.add_argument("--corpus",type=Path,default=ROOT.parent / "repos/C11parser/tests")
    args = parser.parse_args()
    from syntax_mutants import Probe
    audit = load_pyc_audit()
    output = args.summary.resolve().parent / "triage"
    probe = Probe(ROOT / "build/CGNU.cf",ROOT / "grammar/CGNUC11.profile")
    try:
        controls = check_controls(probe,output / "controls",audit)
        summary = json.loads(args.summary.read_text())
        baselines = {item["source"]:item["sha256"] for item in summary["baselines"]}
        rows,unknown = [],[]
        path = output / "current.c"
        for i,record in enumerate(summary["mutants"],1):
            states = [record[k]["accepted"] for k in ("cesta","gcc","clang","pycparser")]
            if len(set(states)) == 1:
                continue
            path.write_bytes(reconstruct(record,baselines,args.corpus))
            families,gaps = mutant_disposition(record,path,audit)
            rows.append({"index":i,"sha256":record["sha256"],"families":families,"unknown":gaps})
            unknown.extend({"index":i,"gap":gap} for gap in gaps)
        result = {"mutants":summary["count"],"discrepancies":len(rows),"controls":controls,
                  "families":dict(Counter(f for row in rows for f in row["families"])),
                  "rows":rows,"unexplained":unknown}
        (output / "summary.json").write_text(json.dumps(result,indent=2)+"\n")
        print(json.dumps({key:result[key] for key in ("mutants","discrepancies","controls","families","unexplained")}))
        if unknown:
            raise SystemExit(1)
    finally:
        probe.close()


if __name__ == "__main__":
    main()
