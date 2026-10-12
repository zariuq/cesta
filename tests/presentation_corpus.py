"""Measure checked native views against current external build configurations."""

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import time

from corpus import ROOT, cetta_units, run, sha, swi_units


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--presentation", default="compact-c/1")
    parser.add_argument("--output", type=Path, default=ROOT / "build/presentation-corpus")
    parser.add_argument("--only", action="append", default=[])
    options = parser.parse_args()
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("qualification outputs belong under cesta/build")
    subprocess.run(["python3", "-B", "tests/corpus_setup.py"], cwd=ROOT, check=True)
    output.mkdir(parents=True, exist_ok=True)
    binary_data = (ROOT / "build/cesta").read_bytes()
    binary = output / ("cesta-"+sha(binary_data)[:16])
    if binary.exists() and binary.read_bytes() != binary_data:
        raise RuntimeError("qualification snapshot hash collision")
    if not binary.exists():
        shutil.copy2(ROOT / "build/cesta", binary)
    results = []
    for corpus in ("cetta", "swi"):
        if corpus == "cetta":
            cwd, units = cetta_units(ROOT / "build/cetta-build-flags.txt")
        else:
            cwd, units, _ = swi_units(ROOT / "build/swipl-build-commands.txt")
        if options.only:
            units = [u for u in units if Path(u["source"]).name in options.only]
        checkout = ROOT.parent / ("hyperon/CeTTa" if corpus == "cetta" else "repos/swipl-devel")
        commit = run(["git", "rev-parse", "HEAD"], checkout)[1].decode().strip()
        tracked = run(["git", "status", "--porcelain", "--untracked-files=no"], checkout)[1].decode()
        directory = output / corpus
        directory.mkdir(exist_ok=True)
        work = directory / "work"
        work.mkdir(exist_ok=True)
        records, constructor_counts, short_counts = [], Counter(), Counter()
        for index, unit in enumerate(units, 1):
            record = dict(unit)
            source = cwd / unit["source"]
            record["source_sha256"] = sha(source.read_bytes())
            record["preprocess_flags"] = ["-g0", "-E", "-P"]
            record["preprocess"], preprocessed = run(unit["compiler"]+unit["flags"]+
                record["preprocess_flags"]+[str(source)], cwd)
            record["preprocessed_sha256"] = sha(preprocessed)
            started = time.monotonic()
            if record["preprocess"]["accepted"]:
                source_file = work / "translation-unit.i"
                source_file.write_bytes(preprocessed)
                metrics_file = work / "metrics.json"
                command = [str(binary), "view", "--as", options.presentation,
                    "--std=c11" if corpus == "cetta" else "--std=gnu11",
                    "--stats-json", str(metrics_file), str(source_file)]
                record["view"], viewed = run(command)
                if record["view"]["accepted"]:
                    metrics = json.loads(metrics_file.read_text())
                    record["metrics"] = metrics
                    record["document_sha256"] = sha(viewed)
                    record["ast_checked_inverse"] = True
                    for row in metrics["by_constructor"]:
                        constructor_counts[row["name"]] += row["occurrences"]
                        short_counts[row["name"]] += row["shortened"]
                else:
                    failure = directory / ("failure-"+str(index))
                    failure.mkdir(exist_ok=True)
                    (failure / "source.i").write_bytes(preprocessed)
            record["view_seconds"] = time.monotonic()-started
            record["source_stable"] = sha(source.read_bytes()) == record["source_sha256"]
            records.append(record)
            metrics = [r["metrics"] for r in records if "metrics" in r]
            counts = {key: sum(m[key] for m in metrics) for key in
                      ("constructors", "shortened", "ordered_lists", "lexical_tokens",
                       "payload_bytes", "envelope_bytes", "C_payload_bytes")}
            coverage = counts["shortened"]/counts["constructors"] if counts["constructors"] else 0
            ratio = counts["payload_bytes"]/counts["C_payload_bytes"] if counts["C_payload_bytes"] else None
            complete = len(records) == len(units) and len(metrics) == len(units)
            summary = {"corpus": corpus, "presentation": options.presentation,
                "binary_sha256": sha(binary_data), "script_sha256": sha(Path(__file__).read_bytes()),
                "checkout": {"head": commit, "tracked_changes": tracked},
                "expected_units": len(units), "units": records, "counts": counts,
                "ast_constructor_coverage": coverage, "nonlayout_payload_ratio": ratio,
                "by_constructor": [{"name": name, "occurrences": count,
                    "shortened": short_counts[name]} for name, count in sorted(constructor_counts.items())],
                "complete": complete,
                "compact_gates_apply": options.presentation == "compact-c/1",
                "compact_gates_pass": complete and coverage >= .95 and ratio <= 1.6 and
                    all(r["source_stable"] for r in records) if options.presentation == "compact-c/1" else None}
            (directory / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
            print(f"{corpus} {index}/{len(units)} {Path(unit['source']).name}: "
                  f"checked={record.get('ast_checked_inverse',False)} "
                  f"aggregate coverage={coverage:.6f} ratio={ratio}", flush=True)
        results.append(summary)
    if not results or any(not r["complete"] or not all(u["source_stable"] for u in r["units"]) or
        (r["compact_gates_apply"] and not r["compact_gates_pass"]) for r in results):
        raise SystemExit("presentation corpus gates are not all satisfied; full counts retained")
    print("presentation corpus: every configured unit satisfies checked inversion; applicable aggregate gates pass")


if __name__ == "__main__":
    main()
