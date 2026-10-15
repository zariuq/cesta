"""Triage independent compiler/parser results without silently changing dialects."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from corpus import CETTA_CHECKOUT, ROOT, pycparser_check, run
from oracle_triage import errors


WITNESSES = {
    "clang:GCC-access-attribute":(
        "extern void f(char*p,unsigned long n)__attribute__((__access__(read_only,1,2)));",
        "extern void f(char*p,unsigned long n);"),
    "clang:GCC-malloc-attribute-signature":(
        "void release(void*); extern void*f(void)__attribute__((__malloc__(release,1)));",
        "void release(void*); extern void*f(void)__attribute__((__malloc__));"),
    "clang:GCC-va-arg-pack-builtin":(
        "extern int g(int,...); int f(int x,...){return g(x,__builtin_va_arg_pack());}",
        "extern int g(int,...); int f(int x,...){return g(x,1);}"),
    "clang:GCC-va-arg-pack-len-builtin":(
        "int f(int x,...){return __builtin_va_arg_pack_len()+x;}",
        "int f(int x,...){return 1+x;}"),
    "clang:GCC-atomic-pointer-builtin":(
        "unsigned f(const _Atomic(unsigned)*p){unsigned r; __atomic_load(p,&r,2);return r;} "
        "_Bool g(_Atomic(_Bool)*p){return __atomic_load_n(p,0);}",
        "unsigned f(const unsigned*p){unsigned r; __atomic_load(p,&r,2);return r;} "
        "_Bool g(_Bool*p){return __atomic_load_n(p,0);}"),
}
for spelling in ("_Float32","_Float64","_Float32x","_Float64x","_Float128"):
    WITNESSES["clang:GCC-floating-type:"+spelling] = (spelling+" x;","double x;")


def clang_family(message):
    if (re.fullmatch(r"address argument to atomic operation must be a pointer to a trivially-copyable type \(.+ invalid\)",message)
        and "_Atomic(" in message):
        return "clang:GCC-atomic-pointer-builtin"
    if message == "unknown attribute '__access__' ignored [-Werror,-Wunknown-attributes]":
        return "clang:GCC-access-attribute"
    if message == "'__malloc__' attribute ignored because Clang does not yet support this attribute signature [-Werror,-Wignored-attributes]":
        return "clang:GCC-malloc-attribute-signature"
    if message in ("use of unknown builtin '__builtin_va_arg_pack' [-Wimplicit-function-declaration]",
                   "use of unknown builtin '__builtin_va_arg_pack_len' [-Wimplicit-function-declaration]"):
        return "clang:GCC-va-arg-pack"+("-len" if "_len'" in message else "")+"-builtin"
    match = re.fullmatch(r"unknown type name '(_Float(?:32|64|32x|64x|128))'",message)
    if match:
        return "clang:GCC-floating-type:"+match[1]
    if message == "too many errors emitted, stopping now [-ferror-limit=]":
        return "clang:diagnostic-truncation"
    return None


def check_controls(output):
    output.mkdir(parents=True,exist_ok=True)
    path = output / "current.c"
    flags = ["-std=c11","-Werror","-fsyntax-only"]
    rows = []
    for family,pair in WITNESSES.items():
        measured = []
        for source in pair:
            path.write_text(source+"\n")
            record = {"source":source}
            for compiler in ("gcc","clang"):
                record[compiler] = run([compiler,*flags,str(path)])[0]
            measured.append(record)
        bad,good = measured
        if (not bad["gcc"]["accepted"] or bad["clang"]["accepted"] or
            not all(good[name]["accepted"] for name in ("gcc","clang")) or
            not any(clang_family(message) == family for message in errors(bad["clang"]))):
            raise RuntimeError(f"independent compiler witness changed: {family}: {measured}")
        rows.append({"family":family,"witness":bad,"control":good})
    pyc_controls = {
        "GNU-attribute":("int x __attribute__((unused));","int x;"),
        "GNU-predefined-type":("typedef __builtin_va_list V;","typedef int V;"),
        "GNU-restrict-spelling":("char *__restrict x;","char *restrict x;"),
        "GNU-extension-prefix":("__extension__ typedef struct {long long q;} S;",
                                "typedef struct {long long q;} S;"),
    }
    for family,pair in pyc_controls.items():
        results = [pycparser_check((source+"\n").encode()) for source in pair]
        if not results[0]["valid_result"] or results[0]["accepted"] or not results[1]["accepted"]:
            raise RuntimeError(f"independent pycparser GNU witness changed: {family}: {results}")
        rows.append({"family":"pycparser:"+family,"witness":{"source":pair[0],"pycparser":results[0]},
                     "control":{"source":pair[1],"pycparser":results[1]}})
    (output / "summary.json").write_text(json.dumps(rows,indent=2)+"\n")
    return len(rows)


def pyc_disposition(record,corpus):
    pyc = record["pycparser"]
    if not pyc["valid_result"]:
        return None,{"reason":"pycparser internal exception lacks a reviewed corpus disposition"}
    diagnostic = pyc["diagnostic"]
    if diagnostic.endswith("before: __attribute__"):
        return "pycparser:GNU-attribute",None
    if ("Missing type in declaration" not in diagnostic and not diagnostic.endswith("before: __dest")
        and not diagnostic.endswith("Invalid function definition")):
        return None,{"reason":"unreviewed pycparser diagnostic","diagnostic":diagnostic}
    # Verify the actual first failing line, rather than infer a builtin from an
    # error message that could equally arise from a Cesta regression.
    cwd = (CETTA_CHECKOUT if corpus == "cetta" else ROOT.parent / "repos/swipl-devel/build")
    source = Path(record["source"])
    before = (cwd / source).read_bytes()
    if hashlib.sha256(before).hexdigest() != record["source_sha256"]:
        return None,{"reason":"qualified source changed before diagnostic provenance check"}
    result,data = run(record["compiler"]+record["flags"]+record["preprocess_flags"]+[str(source)],cwd)
    if not result["accepted"] or hashlib.sha256(data).hexdigest() != record["preprocessed_sha256"]:
        return None,{"reason":"qualified preprocessed source changed before diagnostic provenance check"}
    match = re.match(r"translation-unit.c:(\d+):(\d+):",diagnostic)
    if not match:
        return None,{"reason":"pycparser diagnostic lacks a source position"}
    line = data.decode().splitlines()[int(match[1])-1]
    if diagnostic.endswith("Invalid function definition") and re.match(r"\s*__extension__\s+typedef\b",line):
        return "pycparser:GNU-extension-prefix",{"line":int(match[1]),"text":line}
    if "Missing type in declaration" in diagnostic and "__builtin_va_list" in line:
        return "pycparser:GNU-predefined-type",{"line":int(match[1]),"text":line}
    if diagnostic.endswith("before: __dest") and "__restrict" in line:
        return "pycparser:GNU-restrict-spelling",{"line":int(match[1]),"text":line}
    return None,{"reason":"source line does not establish the reviewed GNU cause","line":line}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summaries",type=Path,nargs="+")
    parser.add_argument("--output",type=Path,default=ROOT / "build/corpus/oracle-triage")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("oracle qualification artifacts belong under build")
    output.mkdir(parents=True,exist_ok=True)
    controls = check_controls(output / "controls")
    rows,unknown = [],[]
    for summary_path in args.summaries:
        summary = json.loads(summary_path.read_text())
        for record in summary["units"]:
            row = {"corpus":summary["corpus"],"source":record["source"],"families":[],"diagnostics":{}}
            if not all(record.get(key,False) for key in ("tree_roundtrip","objects_equal","source_stable")):
                unknown.append({"source":record["source"],"reason":"native corpus gate is not green"})
            for compiler in ("gcc","clang"):
                if record[compiler]["accepted"]:
                    continue
                messages = errors(record[compiler])
                if not messages:
                    unknown.append({"source":record["source"],"reason":compiler+" failed without an error diagnostic"})
                families = []
                for message in messages:
                    if compiler == "clang":
                        family = clang_family(message)
                    elif (message == "this 'if' clause does not guard... [-Werror=misleading-indentation]" and
                          record["baseline_object"]["accepted"] and record["printed_object"]["accepted"]):
                        family = "gcc:preprocessing-layout-warning"
                    else:
                        family = None
                    if not family:
                        unknown.append({"source":record["source"],"reason":compiler+": "+message})
                    else:
                        families.append(family)
                if families == ["clang:diagnostic-truncation"]:
                    unknown.append({"source":record["source"],"reason":"diagnostic truncation alone cannot establish a cause"})
                row["diagnostics"][compiler] = dict(Counter(families))
                row["families"].extend(set(families))
            if not record["pycparser"]["accepted"]:
                family,evidence = pyc_disposition(record,summary["corpus"])
                if family:
                    row["families"].append(family)
                    if evidence: row["pycparser_source_evidence"] = evidence
                else:
                    unknown.append({"source":record["source"],**evidence})
            rows.append(row)
    result = {"translation_unit_configurations":len(rows),"independent_control_pairs":controls,
              "family_translation_units":dict(Counter(family for row in rows for family in row["families"])),
              "diagnostic_counts":dict(Counter(family for row in rows for counts in row["diagnostics"].values()
                                                for family,count in counts.items() for _ in range(count))),
              "rows":rows,"unexplained":unknown}
    (output / "summary.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({key:result[key] for key in ("translation_unit_configurations","independent_control_pairs","family_translation_units","unexplained")}))
    if not rows or unknown:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
