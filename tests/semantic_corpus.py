"""Qualify Cesta's binding, types and conversions on every corpus configuration.

Each unit is preprocessed twice, by GCC and by Clang, and Cesta reads each in
that compiler's dialect. Evidence is reused only while every input agrees:
the Cesta binary, the compilers, the oracle scripts and each preprocessed unit.
"""

import argparse
import collections
import concurrent.futures
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from corpus import ROOT, cetta_units, run, sha, swi_units
import semantic_oracles as oracle
import qualification_cache as cache

SWI_SOURCES = ROOT.parent / "repos/swipl-devel/src"


def configurations():
    cetta_cwd, cetta = cetta_units(ROOT / "build/cetta-build-flags.txt")
    swi_cwd, swi, _ = swi_units(ROOT / "build/swipl-build-commands.txt")
    for corpus, cwd, units in (("cetta", cetta_cwd, cetta), ("swi", swi_cwd, swi)):
        for unit in units:
            source = Path(unit["source"])
            name = source.stem if corpus == "cetta" else str(source.relative_to(SWI_SOURCES)).replace("/", "__")[:-2]
            yield dict(unit, corpus=corpus, unit=name, cwd=str(cwd))


def tool_inputs():
    tools = {}
    for name in ("gcc", "clang"):
        executable = Path(shutil.which(name)).resolve()
        tools[name] = {"sha256": sha(executable.read_bytes()), "version": run([name, "--version"])[1].decode().splitlines()[0]}
    for name in ("cc1", "as"):
        path = Path(run(["gcc", "-print-prog-name="+name])[1].decode().strip())
        if not path.is_absolute():
            path = Path(shutil.which(str(path)))
        tools[name] = {"path": str(path.resolve()), "sha256": sha(path.read_bytes())}
    for name in ("gcc", "clang", "cc1", "as"):
        path = tools[name].get("path", str(Path(shutil.which(name)).resolve()) if shutil.which(name) else "")
        result = subprocess.run(["ldd", path], capture_output=True)
        libraries = re.findall(r"(?:=>\s*)?(/\S+)\s+\(", result.stdout.decode())
        tools[name]["libraries"] = {str(Path(p).resolve()): sha(Path(p).read_bytes()) for p in libraries}
    return tools


def preprocess(unit, compiler):
    return run(([compiler] if compiler == "clang" else unit["compiler"]) + unit["flags"] +
               ["-g0", "-E", "-P", unit["source"]], Path(unit["cwd"]))


def qualify(unit, binary, output, inputs, prior=None):
    """All oracles for one configuration; failures are recorded, never raised."""
    record = cache.context(unit)
    source = Path(unit["cwd"]) / unit["source"]
    record["source_sha256"] = sha(source.read_bytes())
    work = Path(tempfile.mkdtemp(dir=output / "work"))
    keep = False
    try:
        paths = {}
        for compiler in ("gcc", "clang"):
            result, data = preprocess(unit, compiler)
            if not result["accepted"]:
                record[compiler] = {"error": "preprocessing: " + result["diagnostic"][:500]}
                continue
            paths[compiler] = work / (compiler + ".i")
            paths[compiler].write_bytes(data)
            record[compiler + "_preprocessed_sha256"] = sha(data)
        if prior and cache.current(prior, inputs, unit) and all(passes(prior)) and \
                prior.get("source_stable") and prior.get("source_sha256") == record["source_sha256"] and \
                all(c in paths and prior.get(c+"_preprocessed_sha256") == record.get(c+"_preprocessed_sha256")
                    for c in ("gcc", "clang")) and sha(source.read_bytes()) == record["source_sha256"]:
            return prior, True
        if "gcc" in paths:
            try:
                accepted, counts, diagnostic = oracle.probes(binary, unit, paths["gcc"], work, "gcc", "gcc")
                census, unclassified = oracle.census(binary, unit, paths["gcc"])
                record["gcc"] = {"probes_accepted": accepted, "probes": counts, "diagnostic": diagnostic if not accepted else "",
                                 "census": {" ".join(key): value for key, value in census.items()},
                                 "unclassified_warnings": unclassified[:20],
                                 }
                steps, shapes = oracle.arithmetic_steps_and_shapes(binary, unit, paths["gcc"])
                record["gcc"]["arithmetic"] = sorted(steps)
                record["gcc"]["natural_records"] = sorted([kind, size, align, [list(m) for m in members]]
                                                          for kind, size, align, members in shapes)
            except Exception as error:
                record["gcc"] = {"error": str(error)[:1000]}
        if "clang" in paths:
            try:
                tree = oracle.clang_tree(paths["clang"], unit)
                binding = oracle.binding(binary, unit, paths["clang"], tree)
                types_data, _ = oracle.cesta(binary, "types", unit, paths["clang"], dialect="clang")
                conversion_details = []
                conversions = oracle.categories_and_conversions(binary, unit, paths["clang"], tree, types_data, conversion_details)
                del tree
                layouts = oracle.layouts(paths["clang"], unit, types_data)
                accepted, counts, diagnostic = oracle.probes(binary, unit, paths["clang"], work, "clang", "clang")
                explicit = oracle.explicit(binary, unit, paths["clang"], work / "explicit")
                record["clang"] = {"binding": dict(binding), "conversions": dict(conversions), "conversion_disagreements": conversion_details,
                                   "layouts": dict(layouts),
                                   "probes_accepted": accepted,
                                   "probes": counts, "diagnostic": diagnostic if not accepted else "",
                                   "explicit": explicit}
            except Exception as error:
                record["clang"] = {"error": str(error)[:1000]}
        record["source_stable"] = sha(source.read_bytes()) == record["source_sha256"]
        for compiler in ("gcc", "clang"):
            status, data = preprocess(unit, compiler)
            record["source_stable"] &= status["accepted"] and sha(data) == record.get(compiler+"_preprocessed_sha256")
        record["source_stable"] &= sha(source.read_bytes()) == record["source_sha256"]
        keep = not all(passes(record))
        if keep:
            record["failure_directory"] = str(work)
        return cache.receipt(record, inputs, unit), False
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def passes(record):
    gcc, clang = record.get("gcc", {}), record.get("clang", {})
    gcc_ok = "error" not in gcc and gcc.get("probes_accepted") and not gcc.get("census", {}).get("warning unclassified")
    binding, conversions = clang.get("binding", {}), clang.get("conversions", {})
    explicit = clang.get("explicit", {})
    object_evidence = explicit.get("identical_object") or (explicit.get("alignment_differences") and
        all(change.get("only_weaker_alignment") for change in explicit["alignment_differences"]))
    clang_ok = ("error" not in clang and clang.get("probes_accepted") and clang.get("explicit", {}).get("identical_ir") and
                object_evidence and
                not binding.get("disagree") and not binding.get("extra") and not binding.get("labels disagree") and
                not conversions.get("disagree") and not conversions.get("unmatched") and
                not clang.get("layouts", {}).get("disagree") and not clang.get("layouts", {}).get("unmatched"))
    return bool(gcc_ok and record.get("source_stable")), bool(clang_ok and record.get("source_stable"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/semantic-corpus")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--fresh", action="store_true", help="recheck all selected units instead of reusing current evidence")
    parser.add_argument("units", nargs="*")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("qualification products belong under cesta/build")
    (output / "work").mkdir(parents=True, exist_ok=True)
    original_binary = args.binary.resolve()
    binary_bytes = original_binary.read_bytes()
    binary = output / ("cesta-"+sha(binary_bytes)[:16])
    if not binary.exists():
        shutil.copy2(original_binary, binary)
    if binary.read_bytes() != binary_bytes:
        raise RuntimeError("binary snapshot hash collision")
    units = [u for u in configurations() if not args.units or u["unit"] in args.units or u["corpus"] in args.units]
    if not units:
        parser.error("qualification cannot pass an empty selection")

    def input_manifest():
        commands = {token for unit in units for token in unit["compiler"] if shutil.which(token)}
        return {"cesta_sha256": sha(original_binary.read_bytes()), "tools": tool_inputs(), "environment": cache.environment(),
              "configured_compilers": {name: sha(Path(shutil.which(name)).resolve().read_bytes()) for name in commands},
              "scripts": {name: sha((ROOT / "tests" / name).read_bytes())
                          for name in ("semantic_corpus.py", "semantic_oracles.py", "corpus.py", "qualification_cache.py")}}
    inputs = input_manifest()
    summary_path = output / "summary.json"
    previous = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    reusable = {cache.digest(cache.context(r)): r for r in previous.get("units", [])
                if not args.fresh and previous.get("inputs") == inputs}
    records = []
    reused = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending = {}
        for unit in units:
            old = reusable.get(cache.digest(cache.context(unit)))
            pending[pool.submit(qualify, unit, binary, output, inputs, old)] = unit
        for future in concurrent.futures.as_completed(pending):
            record, reuse = future.result()
            reused += reuse
            records.append(record)
            gcc_ok, clang_ok = passes(record)
            print(record["corpus"], record["unit"], "gcc", "ok" if gcc_ok else "FAILED", "clang", "ok" if clang_ok else "FAILED",
                  "(current evidence reused)" if reuse else "(fresh checks)", flush=True)
            summary_path.write_text(json.dumps({"inputs": inputs, "units": records, "all_gates_pass": False}, indent=1)+"\n")
    records.sort(key=lambda r: (r["corpus"], r["unit"]))
    totals = collections.Counter()
    for record in records:
        gcc_ok, clang_ok = passes(record)
        totals["configurations"] += 1
        totals["gcc qualified"] += gcc_ok
        totals["clang qualified"] += clang_ok
        gcc, clang = record.get("gcc", {}), record.get("clang", {})
        for name, value in gcc.get("probes", {}).items():
            totals["gcc " + name] += value
        for name, value in gcc.get("census", {}).items():
            totals["census " + name] += value
        for name, value in clang.get("probes", {}).items():
            totals["clang " + name] += value
        for name, value in clang.get("binding", {}).items():
            totals["binding " + name] += value
        for name, value in clang.get("conversions", {}).items():
            totals["conversions " + name] += value
        for name, value in clang.get("layouts", {}).items():
            totals["layouts " + name] += value
        explicit = clang.get("explicit", {})
        totals["explicit casts"] += explicit.get("casts", 0)
        totals["explicit identical ir"] += bool(explicit.get("identical_ir"))
        totals["explicit raw identical ir"] += bool(explicit.get("raw_identical_ir"))
        totals["explicit alignment differences"] += len(explicit.get("alignment_differences", []))
        totals["explicit identical objects"] += bool(explicit.get("identical_object"))
    steps = sorted({tuple(step) for record in records for step in record.get("gcc", {}).get("arithmetic", [])})
    (output / "arithmetic-steps.txt").write_text("".join(f"{a} {b}\n" for a, b in steps))
    shapes = sorted({json.dumps(shape) for record in records for shape in record.get("gcc", {}).get("natural_records", [])})
    (output / "natural-records.json").write_text("[\n" + ",\n".join(shapes) + "\n]\n")
    failures = [(r["corpus"], r["unit"], r.get("gcc", {}).get("error") or r.get("gcc", {}).get("diagnostic") or "",
                 r.get("clang", {}).get("error") or r.get("clang", {}).get("diagnostic") or "")
                for r in records if not all(passes(r))]
    stable = inputs == input_manifest()
    summary = {"inputs": inputs, "totals": dict(totals), "arithmetic_steps": len(steps), "natural_record_shapes": len(shapes),
               "inputs_stable": stable, "reused_units": reused, "fresh_units": len(records)-reused,
               "all_gates_pass": stable and not failures and len(records) == len(units), "failures": failures, "units": records}
    summary_path.write_text(json.dumps(summary, indent=1) + "\n")
    for key in sorted(totals):
        print(f"{key}: {totals[key]}")
    for failure in failures:
        print("FAILED", failure[0], failure[1], failure[2][:200], failure[3][:200])
    raise SystemExit(0 if summary["all_gates_pass"] else 1)


if __name__ == "__main__":
    main()
