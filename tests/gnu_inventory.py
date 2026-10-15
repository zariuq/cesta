"""Measure GNU constructor occurrences in the actual qualified corpus trees."""

import argparse
from collections import Counter
import json
from pathlib import Path

from corpus import CETTA_CHECKOUT, ROOT, run, sha
from syntax_mutants import Probe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summaries",type=Path,nargs="+")
    parser.add_argument("--output",type=Path,default=ROOT / "build/corpus/gnu-inventory")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("grammar inventory artifacts belong under build")
    output.mkdir(parents=True,exist_ok=True)
    current = output / "current.c"
    results = {}
    for summary_path in args.summaries:
        summary = json.loads(summary_path.read_text())
        corpus = summary["corpus"]
        profile = ROOT / ("grammar/CGNUC11.profile" if corpus == "cetta" else "grammar/CGNU.profile")
        if sha((ROOT / "build/CGNU.cf").read_bytes()) != summary["inputs"]["grammar_sha256"]:
            raise RuntimeError("qualified grammar changed before constructor inventory")
        cwd = (CETTA_CHECKOUT if corpus == "cetta" else ROOT.parent / "repos/swipl-devel/build")
        probe = Probe(ROOT / "build/CGNU.cf",profile)
        total,files = Counter(),Counter()
        rows = []
        try:
            for unit in summary["units"]:
                result,data = run(unit["compiler"]+unit["flags"]+unit["preprocess_flags"]+[unit["source"]],cwd)
                if not result["accepted"] or sha(data) != unit["preprocessed_sha256"]:
                    raise RuntimeError("qualified preprocessing changed before constructor inventory")
                current.write_bytes(data)
                reply = probe.request("I",current)
                if not reply.startswith("I "):
                    raise RuntimeError("qualified constructor inventory failed: "+reply)
                counts = json.loads(reply[2:])
                total.update(counts); files.update(counts.keys())
                rows.append({"source":unit["source"],"constructors":counts})
        finally:
            probe.close()
        results[corpus] = {"units":len(rows),"occurrences":dict(total),"translation_units_using":dict(files),"rows":rows}
    (output / "summary.json").write_text(json.dumps(results,indent=2)+"\n")
    for name,result in results.items():
        print(json.dumps({"corpus":name,"units":result["units"],"GNU_constructors":len(result["occurrences"]),
                          "occurrences":result["occurrences"]}))


if __name__ == "__main__":
    main()
