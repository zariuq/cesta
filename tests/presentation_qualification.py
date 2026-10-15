"""Qualify every presentation and actual host reader on configured C corpora."""

import argparse
from collections import Counter
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import re

from corpus import BUILD_EPOCH, CETTA_CHECKOUT, ROOT, cetta_units, named_workfile, run, sha, swi_units

PRESENTATIONS = ("ast/1", "compact-c/1", "compact-c/2", "cmera-style/1")
COMPACT = ("compact-c/1", "compact-c/2")
# compact-c/2 is gated on naturalness as well: at most this share of its list
# heads may be a grammar constructor name rather than a C spelling.
GRAMMAR_NAME_HEAD_LIMIT = {"compact-c/2": .01}
ATOM = re.compile(rb'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+')


def kebab(label):
    return re.sub(r"(?<!^)(?=[A-Z])", "-", re.sub(r"^GNU", "", label)).lower()


def grammar_name_heads(binary, key, standard):
    """Headed forms whose head only restates a grammar constructor's name."""
    _, data = required([binary, "export-view-rules", "--as", key, standard])
    return {rule["head"] for rule in json.loads(data)["rules"]
            if rule["shape"] == 0 and "-" in rule["head"] and rule["head"] == kebab(rule["name"])}


def head_census(document, names):
    heads, named, after_open = 0, 0, False
    for atom in ATOM.findall(document):
        if after_open and atom not in (b"(", b")"):
            heads += 1
            named += atom.decode(errors="replace") in names
        after_open = atom == b"("
    return {"heads": heads, "grammar_name_heads": named}


def required(arguments, cwd=ROOT, environment=None):
    started = time.monotonic()
    if environment is None:
        record, data = run(list(map(str, arguments)), cwd)
    else:
        result = subprocess.run(list(map(str, arguments)), cwd=cwd, env=environment,
                                capture_output=True, timeout=300)
        record = {"accepted": result.returncode == 0, "status": result.returncode,
                  "diagnostic": result.stderr.decode(errors="replace")[:8192]}
        data = result.stdout
    record["seconds"] = time.monotonic()-started
    if not record["accepted"]:
        raise RuntimeError(f"{arguments}: {record['diagnostic']}")
    return record, data


def snapshot_he(output, python):
    """A private copy of the HE reader (its Python package and extension), taken
    while the files hold still: another build may rewrite the live copy during
    a run, and the run must read one HE throughout."""
    source = ROOT.parent / "hyperon/hyperon-experimental/python"
    def files():
        found = sorted(source.glob("hyperonpy*.so"))
        found += sorted(p for p in (source / "hyperon").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        return found
    for attempt in range(10):
        listed = files()
        before = [(p, p.stat().st_size, p.stat().st_mtime_ns) for p in listed]
        contents = {p.relative_to(source): p.read_bytes() for p in listed}
        if before != [(p, p.stat().st_size, p.stat().st_mtime_ns) for p in files()]:
            time.sleep(10)
            continue
        digest = sha(b"".join(str(k).encode()+b"\0"+v for k, v in sorted(contents.items())))
        target = output / ("he-"+digest[:16])
        if not target.exists():
            partial = output / ("he-"+digest[:16]+".partial")
            shutil.rmtree(partial, ignore_errors=True)
            for name, data in contents.items():
                (partial / name).parent.mkdir(parents=True, exist_ok=True)
                (partial / name).write_bytes(data)
                shutil.copymode(source / name, partial / name)
            partial.rename(target)
        check = subprocess.run([str(python), "-c", "import hyperonpy, hyperon"], capture_output=True,
                               env={**os.environ, "PYTHONPATH": str(target)})
        if check.returncode == 0:
            return target
        time.sleep(10)
    raise RuntimeError("the HE reader kept changing while it was being copied")


def tool_manifest(python, environment):
    files = {}
    for name in ("gcc", "clang", "cc", "as", "swipl"):
        path = Path(shutil.which(name)).resolve()
        files[str(path)] = sha(path.read_bytes())
    frontend = Path(required(["gcc", "-print-prog-name=cc1"])[1].decode().strip()).resolve()
    files[str(frontend)] = sha(frontend.read_bytes())
    runtime = required(["swipl", "--dump-runtime-variables"])[1].decode()
    variables = dict(re.findall(r'^([A-Z_]+)="([^"]*)";', runtime, re.MULTILINE))
    library = Path(variables["PLLIBSWIPL"]).resolve()
    files[str(library)] = sha(library.read_bytes())
    for path in (Path(variables["PLBASE"]) / "library/dcg").rglob("*.pl"):
        files[str(path.resolve())] = sha(path.read_bytes())
    _, loaded = required([python, "-c", "import hyperonpy,hyperon,json;print(json.dumps([hyperonpy.__file__,hyperon.__file__]))"],
                         environment=environment)
    extension, package = map(Path, json.loads(loaded))
    for path in [extension, python, *package.parent.rglob("*.py"),
                 *(ROOT / "build/oracles/tokenizer/tiktoken").glob("*.py"),
                 *(ROOT / "build/oracles/tokenizer/tiktoken").glob("*.so"),
                 *(ROOT / "build/oracles/tokenizer-cache").glob("*")]:
        if path.is_file():
            files[str(path.resolve())] = sha(path.read_bytes())
    for path in [ROOT / "tests" / name for name in
                 ("corpus.py", "corpus_setup.py", "tokenizer_counts.py", "petta_reader_bridge.pl", "he_reader_bridge.py")]:
        files[str(path)] = sha(path.read_bytes())
    for path in (ROOT / "build/cesta", ROOT / "build/cetta-data-reader", Path(__file__)):
        files[str(path)] = sha(path.read_bytes())
    petta_parser = ROOT.parent / "hyperon/PeTTa/src/parser.pl"
    files[str(petta_parser)] = sha(petta_parser.read_bytes())
    return {"files": files, "environment": {name: environment.get(name, "") for name in
        ("PATH", "LD_LIBRARY_PATH", "LD_PRELOAD", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH",
         "SOURCE_DATE_EPOCH", "PYTHONPATH", "TIKTOKEN_CACHE_DIR")}}


def unit_key(unit):
    return json.dumps([unit[key] for key in ("source", "compiler", "flags", "configuration")])


def reusable(record, unit, cwd):
    if any(record.get(key) != unit[key] for key in ("source", "compiler", "flags", "configuration")):
        return False
    if not record.get("qualified", False) or not record.get("source_stable", False):
        return False
    if set(record.get("presentations", {})) != set(PRESENTATIONS):
        return False
    for style in record["presentations"].values():
        if not all(style.get(key, False) for key in ("qualified", "exact_tree", "print_reparse_tree_equal", "matching_object")):
            return False
        if set(style.get("readers", {})) != {"CeTTa", "PeTTa", "HE"}:
            return False
        if not all(reader.get("exact_atom_replay", False) and reader.get("exact_ast", False)
                   for reader in style["readers"].values()):
            return False
        if style.get("object_sha256") != record.get("baseline_object_sha256") or "tokens" not in style:
            return False
    if not all("heads" in record["presentations"][key] for key in COMPACT):
        return False
    switches = {source+" -> "+target for source in PRESENTATIONS for target in PRESENTATIONS}
    if set(record.get("switches", {})) != switches or not all(record["switches"].values()):
        return False
    source = cwd / unit["source"]
    if sha(source.read_bytes()) != record.get("source_sha256"):
        return False
    _, preprocessed = required(unit["compiler"]+unit["flags"]+record["preprocess_flags"]+[source], cwd)
    return sha(preprocessed) == record.get("preprocessed_sha256") and sha(source.read_bytes()) == record["source_sha256"]


def summary(corpus, records, expected, manifest, inputs_stable=False):
    styles = {}
    for key in PRESENTATIONS:
        rows = [record["presentations"][key] for record in records if key in record.get("presentations", {})]
        totals = Counter()
        counts, short = Counter(), Counter()
        for row in rows:
            for name in ("constructors", "shortened", "ordered_lists", "lexical_tokens",
                         "payload_bytes", "envelope_bytes", "C_payload_bytes"):
                totals[name] += row["metrics"][name]
            for name in ("heads", "grammar_name_heads"):
                totals[name] += row.get("heads", {}).get(name, 0)
            for constructor in row["metrics"]["by_constructor"]:
                counts[constructor["name"]] += constructor["occurrences"]
                short[constructor["name"]] += constructor["shortened"]
        coverage = totals["shortened"]/totals["constructors"] if totals["constructors"] else 0
        ratio = totals["payload_bytes"]/totals["C_payload_bytes"] if totals["C_payload_bytes"] else None
        named = totals["grammar_name_heads"]/totals["heads"] if totals["heads"] else None
        limit = GRAMMAR_NAME_HEAD_LIMIT.get(key)
        complete = len(rows) == expected and all(row.get("qualified", False) for row in rows)
        styles[key] = {"units": len(rows), "qualified_units": sum(row.get("qualified", False) for row in rows),
            "counts": dict(totals), "ast_constructor_coverage": coverage,
            "nonlayout_payload_ratio": ratio, "grammar_name_head_share": named, "complete": complete,
            "compact_gates_apply": key in COMPACT,
            "compact_gates_pass": complete and coverage >= .95 and ratio <= 1.6 and
                (limit is None or named <= limit) if key in COMPACT else None,
            "structural": key != "cmera-style/1",
            "by_constructor": [{"name": name, "occurrences": count,
                "shortened": short[name]} for name, count in sorted(counts.items())],
            "tokens": {name: sum(row["tokens"][name] for row in rows if "tokens" in row) for name in
                ("tokens", "payload_tokens", "document_minus_payload_tokens")},
            "build_seconds": {name: sum(row.get(name, {}).get("seconds", 0) for row in rows) for name in
                ("view", "decode", "print", "reparse", "compile")}}
    complete = len(records) == expected and all(row.get("qualified", False) for row in records)
    return {"corpus": corpus, "expected_units": expected, "units": records,
            "presentations": styles, "inputs": manifest, "complete": complete,
            "aggregate_inputs_stable": inputs_stable,
            "all_gates_pass": inputs_stable and complete and all(s["complete"] for s in styles.values()) and
                all(styles[key]["compact_gates_pass"] for key in COMPACT),
            "C_tokens": {name: sum(row.get(name, 0) for row in records) for name in
                         ("source_tokens", "printed_tokens")}}


def qualify(unit, cwd, corpus, work, binary, host_commands, environment):
    record = dict(unit)
    record["build_environment"] = {"SOURCE_DATE_EPOCH": BUILD_EPOCH}
    source = cwd / unit["source"]
    record["source_sha256"] = sha(source.read_bytes())
    record["preprocess_flags"] = ["-g0", "-E", "-P"]
    record["preprocess"], preprocessed = required(unit["compiler"]+unit["flags"]+
        record["preprocess_flags"]+[source], cwd)
    record["preprocessed_sha256"] = sha(preprocessed)
    source_file = work / "translation-unit.i"
    source_file.write_bytes(preprocessed)
    standard = "--std=c11" if corpus == "cetta" else "--std=gnu11"
    record["read"], ast = required([binary, "read", standard, source_file])
    record["ast_sha256"] = sha(ast)
    record["baseline_compile"], _ = required(unit["compiler"]+unit["flags"]+
        ["-g0", "-c", source, "-o", work / "native.o"], cwd)
    baseline_hash = sha((work / "native.o").read_bytes())
    record["baseline_object_sha256"] = baseline_hash
    documents = {}
    record["presentations"] = {}
    names = {key: grammar_name_heads(binary, key, standard) for key in COMPACT}
    printed_hash = None
    printed_path = None
    for key in PRESENTATIONS:
        name = key.replace("/", "-")
        path = work / (name+".cesta")
        metrics_path = work / (name+".metrics.json")
        style = record["presentations"][key] = {}
        style["view"], document = required([binary, "view", standard, "--as", key,
            "--stats-json", metrics_path, source_file])
        style["metrics"] = json.loads(metrics_path.read_text())
        if key in COMPACT:
            style["heads"] = head_census(document, names[key])
        path.write_bytes(document)
        documents[key] = document
        style["document_sha256"] = sha(document)
        ast_path = work / "decoded.csexp"
        style["decode"], decoded = required([binary, "convert", standard, "--to", "ast/1", path])
        ast_path.write_bytes(decoded)
        style["exact_tree"] = True
        if key == "ast/1" and decoded != document:
            raise RuntimeError("AST document changed on normalization")
        if key != "ast/1" and decoded != documents["ast/1"]:
            raise RuntimeError(key+" changed the AST")
        style["print"], printed = required([binary, "write", standard, path])
        emitted = named_workfile(work / name, source.name)
        emitted.write_bytes(printed)
        style["reparse"], tree = required([binary, "read", standard, emitted])
        if tree != ast:
            raise RuntimeError(key+" changed the source-admitted tree")
        style["print_reparse_tree_equal"] = True
        style["printed_sha256"] = sha(printed)
        if printed_hash is not None and printed_hash != sha(printed):
            raise RuntimeError("equal presentation trees produced different C")
        printed_hash, printed_path = sha(printed), emitted
        style["compile"], _ = required(unit["compiler"]+unit["flags"]+
            ["-g0", "-c", emitted, "-o", work / "printed.o"], cwd)
        style["object_sha256"] = sha((work / "printed.o").read_bytes())
        if style["object_sha256"] != baseline_hash:
            raise RuntimeError(key+" changed the native object")
        style["matching_object"] = True
        style["readers"] = {}
        _, wire = required([binary, "pack", path])
        wire_path = work / "wire.metta"
        wire_path.write_bytes(wire)
        style["portable_document_bytes"] = len(wire)
        for reader, command in host_commands.items():
            host, data = required([wire_path if argument == "{input}" else argument for argument in command], environment=environment)
            replay_path = work / "host-replay.metta"
            replay_path.write_bytes(data)
            _, unpacked = required([binary, "unpack", replay_path])
            if unpacked != document:
                raise RuntimeError(reader+" changed atom kinds, spelling or ordered children")
            _, replayed_tree = required([binary, "convert", standard, "--to", "ast/1", replay_path])
            if replayed_tree != documents["ast/1"]:
                raise RuntimeError(reader+" changed the presentation's AST")
            style["readers"][reader] = {"seconds": host["seconds"], "exact_atom_replay": True,
                "exact_ast": True, "wire_sha256": sha(data)}
        style["qualified"] = True
    record["switches"] = {}
    for source_key in PRESENTATIONS:
        source_path = work / (source_key.replace("/", "-")+".cesta")
        for target_key in PRESENTATIONS:
            _, converted = required([binary, "convert", standard, "--to", target_key, source_path])
            if converted != documents[target_key]:
                raise RuntimeError(source_key+" -> "+target_key+" did not normalize exactly")
            record["switches"][source_key+" -> "+target_key] = True
    _, data = required([environment["CESTA_TOKENIZER_PYTHON"], ROOT / "tests/tokenizer_counts.py",
        source_file, printed_path, *[work / (key.replace("/", "-")+".cesta") for key in PRESENTATIONS]],
        environment=environment)
    tokens = json.loads(data)
    record["tokenizer"] = tokens["tokenizer"]
    record["source_tokens"], record["printed_tokens"] = [row["tokens"] for row in tokens["records"][:2]]
    for key, counts in zip(PRESENTATIONS, tokens["records"][2:]):
        record["presentations"][key]["tokens"] = counts
    record["source_stable"] = sha(source.read_bytes()) == record["source_sha256"]
    if not record["source_stable"]:
        raise RuntimeError("corpus source changed during qualification")
    record["qualified"] = True
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/presentation-qualified")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--corpus", choices=("cetta", "swi"), action="append", default=[])
    parser.add_argument("--reuse", action="store_true", help="reuse completed cases only after input and preprocessor revalidation")
    parser.add_argument("--fresh", action="store_true", help="recheck all selected units even when completed evidence is current")
    parser.add_argument("--petta-stack-limit", help="optional Prolog process stack budget")
    parser.add_argument("--jobs", type=int, default=4, help="units qualified at once")
    options = parser.parse_args()
    if options.jobs < 1:
        parser.error("--jobs must be positive")
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("qualification products belong under cesta/build")
    output.mkdir(parents=True, exist_ok=True)
    required(["python3", "-B", ROOT / "tests/corpus_setup.py"])
    python_installations = sorted((ROOT / "build/oracles/python311").glob("*/bin/python3.11"))
    if len(python_installations) != 1:
        raise RuntimeError("provide the existing HE-compatible test interpreter")
    python = python_installations[0]
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "SOURCE_DATE_EPOCH": BUILD_EPOCH,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "build/oracles/tokenizer"), str(snapshot_he(output, python))]),
        "TIKTOKEN_CACHE_DIR": str(ROOT / "build/oracles/tokenizer-cache"),
        "CESTA_TOKENIZER_PYTHON": str(python)})
    binary_bytes = (ROOT / "build/cesta").read_bytes()
    binary = output / ("cesta-"+sha(binary_bytes)[:16])
    if not binary.exists():
        shutil.copy2(ROOT / "build/cesta", binary)
    if binary.read_bytes() != binary_bytes:
        raise RuntimeError("binary snapshot hash collision")
    hosts = {
        "CeTTa": [ROOT / "build/cetta-data-reader", "{input}", "--dump"],
        "PeTTa": ["swipl", *(["--stack_limit="+options.petta_stack_limit] if options.petta_stack_limit else []),
                  "-q", "-s", ROOT / "tests/petta_reader_bridge.pl", "--",
                  ROOT.parent / "hyperon/PeTTa/src/parser.pl", "{input}"],
        "HE": [python, ROOT / "tests/he_reader_bridge.py", "{input}"]}
    tools = tool_manifest(python, environment)
    suites = []
    for corpus in options.corpus or ("cetta", "swi"):
        cwd, units = cetta_units(ROOT / "build/cetta-build-flags.txt") if corpus == "cetta" else \
            swi_units(ROOT / "build/swipl-build-commands.txt")[:2]
        if options.only:
            units = [unit for unit in units if Path(unit["source"]).name in options.only]
        if not units:
            raise RuntimeError("qualification cannot pass an empty corpus")
        directory = output / corpus
        directory.mkdir(exist_ok=True)
        work = directory / "work"
        work.mkdir(exist_ok=True)
        checkout = (CETTA_CHECKOUT if corpus == "cetta" else ROOT.parent / "repos/swipl-devel")
        manifest = {"binary_sha256": sha(binary_bytes), "script_sha256": sha(Path(__file__).read_bytes()),
            "head": required(["git", "rev-parse", "HEAD"], checkout)[1].decode().strip(),
            "tracked_changes": required(["git", "status", "--porcelain", "--untracked-files=no"], checkout)[1].decode(),
            "tools": tools, "reader_commands": {name: list(map(str, command)) for name, command in hosts.items()},
            "reader_bridge_sha256": {name: sha((ROOT / filename).read_bytes()) for name, filename in
                (("CeTTa", "build/cetta-data-reader"), ("PeTTa", "tests/petta_reader_bridge.pl"), ("HE", "tests/he_reader_bridge.py"))}}
        summary_path = directory / "summary.json"
        previous = json.loads(summary_path.read_text()) if options.reuse and not options.fresh and summary_path.exists() else {}
        cached = {unit_key(record): record for record in previous.get("units", [])} \
            if previous.get("inputs") == manifest else {}

        def check(index, unit):
            # Each unit has its own working directory, so units run side by side.
            unit_work = work / str(index)
            unit_work.mkdir(exist_ok=True)
            prior = cached.get(unit_key(unit), {})
            if prior and reusable(prior, unit, cwd):
                return prior, True
            return qualify(unit, cwd, corpus, unit_work, binary, hosts, environment), False

        done, failure = {}, None
        with concurrent.futures.ThreadPoolExecutor(max_workers=options.jobs) as pool:
            pending = {pool.submit(check, index, unit): index for index, unit in enumerate(units, 1)}
            for future in concurrent.futures.as_completed(pending):
                index = pending[future]
                unit = units[index-1]
                if future.cancelled():
                    continue
                try:
                    done[index], reuse = future.result()
                except Exception as error:
                    done[index] = {**unit, "qualified": False, "failure": str(error)}
                    destination = directory / ("failure-"+str(index))
                    if not destination.exists():
                        shutil.copytree(work / str(index), destination)
                    failure = failure or error
                    for waiting in pending:
                        waiting.cancel()
                    continue
                records = [done[key] for key in sorted(done)]
                (directory / "summary.json").write_text(json.dumps(summary(corpus, records, len(units), manifest), indent=2)+"\n")
                print(f"{corpus} {index}/{len(units)} {Path(unit['source']).name}: "+
                      ("unchanged inputs revalidate the complete evidence" if reuse else
                       "four presentations, sixteen switches, twelve host-reader replays and identical native objects"), flush=True)
        records = [done[key] for key in sorted(done)]
        if failure is not None:
            (directory / "summary.json").write_text(json.dumps(summary(corpus, records, len(units), manifest), indent=2)+"\n")
            raise failure
        for record, unit in zip(records, units):
            if not reusable(record, unit, cwd):
                raise RuntimeError("corpus source or preprocessing inputs changed before aggregate qualification")
        if tools != tool_manifest(python, environment):
            raise RuntimeError("compiler, reader or tokenizer inputs changed during qualification")
        state = summary(corpus, records, len(units), manifest, inputs_stable=True)
        summary_path.write_text(json.dumps(state, indent=2)+"\n")
        suites.append(state)
    if not all(state["all_gates_pass"] for state in suites):
        raise SystemExit("one or more presentation qualification gates failed")
    print("presentation qualification: all configured trees, style switches, source reparses, native objects, portable readers and applicable compactness gates passed")


if __name__ == "__main__":
    main()
