"""Compiler and codec comparison for the external C11parser test corpus."""

import argparse
import hashlib
import json
from pathlib import Path
from corpus import run, pycparser_check
from oracle_triage import load_pyc_audit
from c11_intended import READINGS, verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--grammar", default="build/CGNU.cf")
    parser.add_argument("--profile", default="grammar/CGNUC11.profile")
    options = parser.parse_args()
    output = Path("build/c11parser")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    unknown = []
    audit = load_pyc_audit()
    dispositions = {"atomic_parenthesis.c":"declared-atomic-parenthesis-restriction",
                    "dangling_else_misleading.fail.c":"scope-and-syntax-negative",
                    "bitfield_declaration_ambiguity.fail.c":"member-resolution-constraint-negative"}
    shapes = {
        "typedef_star.c": [("TypedefType",1)],
        "variable_star.c": [("Multiply",1)],
        "declarator_visibility.c": [("TypedefType",2),("SizeofExpression",1)],
        "parameter_declaration_ambiguity.c": [("NamedParameter",2),("AbstractParameter",2)],
        "enum_shadows_typedef.c": [("TypedefType",0),("EnumDefinition",1)],
        "bitfield_declaration_ambiguity.fail.c": [("NoDeclarator",1),("TypedefType",1)],
        "bitfield_declaration_ambiguity.ok.c": [("NoDeclarator",0),("TypedefType",0)],
    }
    for source in sorted(options.corpus.glob("*.c")):
        record = {"name": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "declared_negative": ".fail." in source.name}
        for compiler in ("gcc", "clang"):
            record[compiler],_ = run([compiler, "-std=c11", "-fsyntax-only", str(source)])
        preprocessed,data = run(["gcc", "-std=c11", "-E", "-P", str(source)])
        record["preprocessed"] = preprocessed["accepted"]
        if not record["preprocessed"]:
            record["preprocessor_diagnostic"] = preprocessed["diagnostic"]
            unknown.append(source.name+": preprocessing failed")
            results.append(record)
            continue
        path = output / source.name
        path.write_bytes(data)
        record["preprocessed_sha256"] = hashlib.sha256(data).hexdigest()
        record["pycparser"] = pycparser_check(data)
        cesta = ["build/cesta", "read", options.grammar, str(path), "TranslationUnit", options.profile]
        record["cesta"],wire_data = run(cesta)
        expected_reader = source.name not in ("atomic_parenthesis.c","dangling_else_misleading.fail.c")
        expected_compiler = source.name not in dispositions
        record["disposition"] = dispositions.get(source.name,"positive")
        if (record["cesta"]["accepted"] != expected_reader or
            any(record[name]["accepted"] != expected_compiler for name in ("gcc","clang"))):
            unknown.append(source.name+": stage admission changed")
        if record["cesta"]["accepted"]:
            if expected_compiler:
                if source.name not in READINGS:
                    unknown.append(source.name+": missing independent intended-reading controls")
                else:
                    try:
                        record["intended_reading_checks"] = verify(source.name,wire_data)
                    except (ValueError, AssertionError) as error:
                        unknown.append(str(error))
            wire = path.with_suffix(".csexp")
            wire.write_bytes(wire_data)
            print_result,printed = run(["build/cesta", "write", options.grammar, str(wire), "TranslationUnit", options.profile])
            record["printed"] = print_result["accepted"]
            if record["printed"]:
                path.write_bytes(printed)
                reread,rewire = run(cesta)
                record["tree_roundtrip"] = reread["accepted"] and rewire == wire_data
                if not record["tree_roundtrip"]:
                    unknown.append(source.name+": codec tree changed")
                for compiler in ("gcc", "clang"):
                    record[compiler + "_printed"],_ = run([compiler, "-std=c11", "-fsyntax-only", str(path)])
                    if record[compiler+"_printed"]["accepted"] != expected_compiler:
                        unknown.append(source.name+": printed compiler admission changed")
                if source.name in shapes:
                    record["intended_shapes"] = []
                    for label,expected in shapes[source.name]:
                        actual = wire_data.count(("(c:"+label+" ").encode())+wire_data.count(("(c:"+label+")").encode())
                        record["intended_shapes"].append({"label":label,"expected":expected,"actual":actual})
                        if actual != expected:
                            unknown.append(source.name+": intended "+label+" reading changed")
                if record["declared_negative"]:
                    for compiler in ("gcc","clang"):
                        build,_ = run(["build/cesta","cc","--cc",compiler,"-std=c11","-fsyntax-only",str(wire)])
                        record["build_"+compiler] = build
                        if build["accepted"]:
                            unknown.append(source.name+": negative accepted by Cesta compiler workflow")
            else:
                record["printer_diagnostic"] = print_result["diagnostic"]
                unknown.append(source.name+": printer failed")
        pyc = record["pycparser"]
        if pyc["accepted"] != record["cesta"]["accepted"] or not pyc["valid_result"]:
            reviewed = audit["baselines"].get(record["preprocessed_sha256"])
            if reviewed and pyc == reviewed["pycparser"]:
                record["pycparser_disposition"] = reviewed["family"]
            else:
                unknown.append(source.name+": pycparser result outside source-hashed audit")
        results.append(record)
    (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    totals = {"files": len(results), "gcc": sum(r["gcc"]["accepted"] for r in results),
        "clang": sum(r["clang"]["accepted"] for r in results),
        "cesta": sum(r.get("cesta", {}).get("accepted", False) for r in results),
        "tree_roundtrips": sum(r.get("tree_roundtrip", False) for r in results),
        "syntax_negatives":sum(not r.get("cesta",{}).get("accepted",False) for r in results),
        "constraint_negatives":sum(r["disposition"] == "member-resolution-constraint-negative" for r in results),
        "intended_valid_files":sum(bool(r.get("intended_reading_checks")) for r in results),
        "independent_intended_checks":sum(len(r.get("intended_reading_checks",[])) for r in results),
        "intended_shape_checks":sum(len(r.get("intended_shapes",[])) for r in results),"unexplained":unknown}
    (output / "qualification.json").write_text(json.dumps(totals,indent=2)+"\n")
    print(json.dumps(totals))
    if not results or unknown:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
