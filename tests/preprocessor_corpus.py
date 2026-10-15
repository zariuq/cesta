"""Preprocessor corpus: Cesta's translation phase 4 against GCC's and
Clang's on every configured translation unit.

For each unit and each compiler, Cesta's preprocessor runs configured as
that compiler (its predefined macros, include directories and __has_*
answers come from the compiler itself). Its output must have the same
preprocessing tokens as the compiler's own -E -P, and the compiler must
build the same object from Cesta's line-marked output as from the source.
"""

import argparse
import collections
import concurrent.futures
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from corpus import BUILD_EPOCH, ROOT, run, sha
import qualification_cache as cache
from semantic_corpus import configurations, tool_inputs
from source_qualification import source_state


def environment():
    """The compilers' environment (corpus.run sets SOURCE_DATE_EPOCH): __DATE__
    and __TIME__ must read the same instant."""
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = BUILD_EPOCH
    return env


def standard(unit):
    for flag in unit["flags"]:
        if flag.startswith("-std="):
            return "c11" if flag[5:].startswith(("c", "iso")) else "gnu11"
    return "gnu11"


def compiler_command(unit, compiler):
    return [compiler] if compiler == "clang" else unit["compiler"]


def tokens(binary, path, mode):
    result = subprocess.run([str(binary), "source", "--std="+mode, "--tokens", str(path)], capture_output=True)
    if result.returncode:
        raise RuntimeError("token scan: "+result.stderr.decode(errors="replace")[:500])
    return result.stdout.splitlines()


PREPROCESSOR_ONLY = ("-I", "-D", "-U", "-include", "-imacros", "-isystem", "-iquote", "-idirafter",
                     "-MD", "-MMD", "-MF", "-MT", "-MQ", "-MP")
WITH_ARGUMENT = ("-I", "-D", "-U", "-include", "-imacros", "-isystem", "-iquote", "-idirafter", "-MF", "-MT", "-MQ")


def compile_flags(flags):
    """The flags that still act on preprocessed input: preprocessor options
    have nothing left to do there (and Clang reports them as unused)."""
    kept, skip = [], False
    for flag in flags:
        if skip:
            skip = False
            continue
        if flag in WITH_ARGUMENT:
            skip = True
            continue
        if flag.startswith(PREPROCESSOR_ONLY):
            continue
        kept.append(flag)
    return kept


def strip_markers(data):
    return b"\n".join(line for line in data.split(b"\n") if not (line.startswith(b"# ") and line[2:3].isdigit()))


def qualify(unit, binary, output, inputs, prior=None):
    record = cache.context(unit)
    cwd = Path(unit["cwd"])
    source = cwd / unit["source"]
    record["source_sha256"] = sha(source.read_bytes())
    work = Path(tempfile.mkdtemp(dir=output / "work"))
    keep = False
    mode = standard(unit)
    try:
        record["source_state"] = source_state(unit, work)
        if prior and cache.current(prior, inputs, unit) and passes(prior) and \
                prior.get("source_sha256") == record["source_sha256"] and \
                prior.get("source_state") == record["source_state"] and \
                sha(source.read_bytes()) == record["source_sha256"]:
            return prior, True
        for compiler in ("gcc", "clang"):
            command = compiler_command(unit, compiler)
            row = record[compiler] = {}
            try:
                status, reference = run(command + unit["flags"] + ["-g0", "-E", "-P", unit["source"]], cwd)
                if not status["accepted"]:
                    row["error"] = "compiler preprocessing: "+status["diagnostic"][:500]; continue
                (work / (compiler+"-reference.i")).write_bytes(reference)
                ours = subprocess.run([str(binary), "pp", "--cc", command[0], *command[1:], *unit["flags"], "-g0", "-P",
                                       unit["source"]], cwd=cwd, capture_output=True, timeout=600, env=environment())
                if ours.returncode:
                    row["error"] = "cesta pp: "+ours.stderr.decode(errors="replace")[:500]; continue
                (work / (compiler+"-cesta.i")).write_bytes(ours.stdout)
                expected = tokens(binary, work / (compiler+"-reference.i"), mode)
                actual = tokens(binary, work / (compiler+"-cesta.i"), mode)
                row["tokens"] = len(expected)
                row["tokens_equal"] = expected == actual
                if not row["tokens_equal"]:
                    at = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual)))
                    row["first_difference"] = {"index": at, "compiler": [x.decode(errors="replace") for x in expected[at:at+4]],
                                               "cesta": [x.decode(errors="replace") for x in actual[at:at+4]]}
                # The compiler builds the same object from Cesta's line-marked output.
                marked = subprocess.run([str(binary), "pp", "--cc", command[0], *command[1:], *unit["flags"], "-g0",
                                         unit["source"]], cwd=cwd, capture_output=True, timeout=600, env=environment())
                if marked.returncode:
                    row["error"] = "cesta pp (markers): "+marked.stderr.decode(errors="replace")[:500]; continue
                preprocessed = work / (Path(unit["source"]).stem+".i")
                preprocessed.write_bytes(marked.stdout)
                base, _ = run(command + unit["flags"] + ["-Wno-error", "-g0", "-c", unit["source"], "-o", work / (compiler+"-base.o")], cwd)
                # Warnings that depend on macro-expansion locations cannot be told
                # apart in preprocessed text; they change diagnostics, not code.
                built, _ = run(command + compile_flags(unit["flags"]) + ["-Wno-error", "-g0", "-c", preprocessed, "-o",
                                                                       work / (compiler+"-cesta.o")], cwd)
                if not base["accepted"] or not built["accepted"]:
                    row["error"] = "object: "+(base["diagnostic"] or built["diagnostic"])[:500]; continue
                row["identical_object"] = sha((work / (compiler+"-base.o")).read_bytes()) == sha((work / (compiler+"-cesta.o")).read_bytes())
            except Exception as error:
                row["error"] = str(error)[:500]
        record["source_stable"] = (sha(source.read_bytes()) == record["source_sha256"] and
                                   source_state(unit, work) == record["source_state"])
        keep = not passes(record)
        if keep:
            record["failure_directory"] = str(work)
        return cache.receipt(record, inputs, unit), False
    except Exception as error:
        keep = True
        record["source_stable"] = False
        record["source_error"] = str(error)[:1000]
        record["failure_directory"] = str(work)
        return cache.receipt(record, inputs, unit), False
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def passes(record):
    return bool(record.get("source_stable")) and all(
        "error" not in record.get(c, {}) and record.get(c, {}).get("tokens_equal") and record.get(c, {}).get("identical_object")
        for c in ("gcc", "clang"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/preprocessor-corpus")
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
    original = args.binary.resolve()
    binary_bytes = original.read_bytes()
    binary = output / ("cesta-"+sha(binary_bytes)[:16])
    if not binary.exists():
        shutil.copy2(original, binary)
    if binary.read_bytes() != binary_bytes:
        raise RuntimeError("binary snapshot hash collision")
    units = [u for u in configurations() if not args.units or u["unit"] in args.units or u["corpus"] in args.units]
    if not units:
        parser.error("qualification cannot pass an empty selection")

    def input_manifest():
        commands = {token for unit in units for token in unit["compiler"] if shutil.which(token)}
        return {"cesta_sha256": sha(original.read_bytes()), "tools": tool_inputs(), "environment": cache.environment(),
                "configured_compilers": {name: sha(Path(shutil.which(name)).resolve().read_bytes()) for name in commands},
                "scripts": {name: sha((ROOT / "tests" / name).read_bytes())
                            for name in ("preprocessor_corpus.py", "semantic_corpus.py", "corpus.py",
                                         "qualification_cache.py", "source_qualification.py")}}
    inputs = input_manifest()
    summary_path = output / "summary.json"
    previous = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    reusable = {cache.digest(cache.context(r)): r for r in previous.get("units", [])
                if not args.fresh and previous.get("inputs") == inputs}
    records, reused = [], 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(qualify, unit, binary, output, inputs, reusable.get(cache.digest(cache.context(unit)))): unit
                   for unit in units}
        for future in concurrent.futures.as_completed(pending):
            record, reuse = future.result()
            reused += reuse
            records.append(record)
            print(record["corpus"], record["unit"], "ok" if passes(record) else "FAILED",
                  "(current evidence reused)" if reuse else "(fresh checks)", flush=True)
            summary_path.write_text(json.dumps({"inputs": inputs, "units": records, "all_gates_pass": False}, indent=1)+"\n")
    records.sort(key=lambda r: (r["corpus"], r["unit"]))
    totals = collections.Counter()
    for record in records:
        totals["configurations"] += 1
        for c in ("gcc", "clang"):
            row = record.get(c, {})
            totals[c+" token streams identical"] += bool(row.get("tokens_equal"))
            totals[c+" identical objects"] += bool(row.get("identical_object"))
            totals[c+" tokens"] += row.get("tokens", 0)
    failures = [(r["corpus"], r["unit"], {c: {k: v for k, v in r.get(c, {}).items() if k in ("error", "first_difference", "identical_object")}
                                          for c in ("gcc", "clang")}, r.get("failure_directory", ""))
                for r in records if not passes(r)]
    stable = inputs == input_manifest()
    summary = {"inputs": inputs, "totals": dict(totals), "inputs_stable": stable, "reused_units": reused,
               "fresh_units": len(records)-reused, "all_gates_pass": stable and not failures and len(records) == len(units),
               "failures": failures, "units": records}
    summary_path.write_text(json.dumps(summary, indent=1)+"\n")
    for key in sorted(totals):
        print(f"{key}: {totals[key]}")
    for failure in failures[:40]:
        print("FAILED", failure[0], failure[1], json.dumps(failure[2])[:400], failure[3])
    raise SystemExit(0 if summary["all_gates_pass"] else 1)


if __name__ == "__main__":
    main()
