"""Provenance corpus: Cesta's positions in the original sources against the
compilers' own.

For every configured unit, Cesta preprocesses the source and records, for each
token of its output, where the token was spelled and the macro expansions that
produced it. Two compilers then locate the same constructs in the original
source, each by its own conventions:

- GCC's conversion warnings (-Wconversion and its relatives) give a file, line
  and column; Cesta's map turns each into offsets of its own preprocessed text,
  and every warning must fall on a conversion Cesta's census reports there.
- Clang's AST gives every reference's spelling and expansion locations; each must
  name a token Cesta binds to the declaration Clang names, at the location
  Clang gives for that declaration.

GCC's diagnostics name where a token was spelled; Clang's AST pairs that with
the point where the outermost macro was expanded.
"""

import argparse
import collections
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from corpus import BUILD_EPOCH, ROOT, sha
import semantic_oracles as oracle
from semantic_corpus import configurations, tool_inputs
from preprocessor_corpus import compiler_command, standard
import qualification_cache as cache
from source_qualification import source_state

PROVENANCE = re.compile(r'^(\d+) (\d+) ("(?:[^"\\]|\\.)*") (\d+) (\d+)(.*)$')
EXPANSION = re.compile(r' ("(?:[^"\\]|\\.)*") ("(?:[^"\\]|\\.)*") (\d+) (\d+)')
WARNING = re.compile(r"^(.*?):(\d+):(\d+): warning: (.*?) \[(-W[^\]]+)\]$", re.M)
SCRATCH = "<scratch space>"


def unquote(text):
    return json.loads(text)


def provenance(path, convention):
    """offset -> (spelling, point). GCC's diagnostics name where a token was
    spelled (inside a macro's definition when it came from one) and add the
    expansion as a note; Clang's AST pairs the spelling with the point where the
    outermost macro was expanded, also for a token substituted as an argument."""
    rows = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        match = PROVENANCE.match(line)
        if not match:
            raise RuntimeError("malformed provenance line: "+line[:200])
        offset, _, file, row, column, chains = match.groups()
        pasted = chains.startswith(" ##")
        body, _, argument = chains[3 if pasted else 0:].partition(" ;")
        # A token formed by ## is spelled in no file; Clang calls its spelling
        # the scratch space and identifies it by its expansion point.
        spelling = (SCRATCH, 0, 0) if pasted and convention == "clang" else (unquote(file), int(row), int(column))
        if convention == "clang":
            expansions = EXPANSION.findall(body) or EXPANSION.findall(argument)
            point = (unquote(expansions[-1][1]), int(expansions[-1][2]), int(expansions[-1][3])) if expansions else spelling
            rows[int(offset)] = (spelling, point)
        else:
            # GCC names the spelling, or an expansion point when the spelling is
            # in a system header or the expression's range leaves the macro.
            points = {spelling} | {(unquote(f), int(l), int(c)) for _, f, l, c in EXPANSION.findall(body+argument)}
            rows[int(offset)] = (spelling, points)
    return rows


def environment():
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = BUILD_EPOCH
    return env


def preprocess(binary, unit, compiler, work):
    command = compiler_command(unit, compiler)
    text, mapping = work / (compiler+".i"), work / (compiler+".map")
    result = subprocess.run([str(binary), "pp", "--cc", command[0], *command[1:], *unit["flags"], "-g0", "-P",
                             "-o", str(text), "--provenance", str(mapping), unit["source"]],
                            cwd=unit["cwd"], capture_output=True, timeout=600, env=environment())
    if result.returncode:
        raise RuntimeError("cesta pp: "+result.stderr.decode(errors="replace")[:500])
    return text, provenance(mapping, compiler)


def source_flags(unit):
    """The unit's own flags for its original source; warnings stay warnings."""
    return [flag for flag in unit["flags"] if flag != "-w" and not flag.startswith("-Werror")]


def warning_locations(unit):
    result = subprocess.run([*compiler_command(unit, "gcc"), *source_flags(unit), "-fsyntax-only", "-Wconversion",
                             "-Wsign-conversion", "-Wfloat-conversion", "-fdiagnostics-column-unit=byte",
                             "-fno-diagnostics-show-caret", unit["source"]], cwd=unit["cwd"], capture_output=True)
    if result.returncode:
        raise RuntimeError("gcc: "+result.stderr.decode(errors="replace")[-500:])
    return [(file, int(line), int(column), message) for file, line, column, message, _ in WARNING.findall(result.stderr.decode(errors="replace"))]


def census_gate(binary, unit, work):
    text, origins = preprocess(binary, unit, "gcc", work)
    data, _ = oracle.cesta(binary, "census", unit, text)
    conversions = [tuple(map(int, (b, e, pb, pe))) for b, e, _, pb, pe in oracle.CENSUS.findall(data)]
    source = text.read_bytes()
    def opening(begin):
        while begin > 0 and source[begin-1:begin] in (b"(", b" ", b"\t", b"\n"):
            begin -= 1
        return begin
    at = collections.defaultdict(list)
    for offset, (spelling, points) in origins.items():
        for point in points:
            at[point].append(offset)
    counts, unmatched = collections.Counter(), []
    for file, line, column, message in warning_locations(unit):
        names = [aka or name for name, aka in re.findall(r"‘([^’]+)’(?: \{aka ‘([^’]+)’\})?", message)]
        if len(names) >= 2 and names[0] == names[1]:
            counts["operand shortening"] += 1
            continue
        offsets = at.get((file, line, column), [])
        if not offsets:
            counts["unlocated"] += 1; unmatched.append((file, line, column, message[:100])); continue
        if any(opening(b) <= offset < max(e, b+1) or pb <= offset < pe for offset in offsets for b, e, pb, pe in conversions):
            counts["classified at its source position"] += 1
        else:
            counts["unclassified"] += 1; unmatched.append((file, line, column, message[:100]))
    return counts, unmatched[:20]


def clang_locations(tree):
    """Each JSON location with its elided file and line restored: Clang writes
    a location's file and line only when they change, in document order."""
    state = {"file": None, "line": None}
    def bare(location):
        if "file" in location: state["file"] = location["file"]; state["line"] = None
        if "line" in location: state["line"] = location["line"]
        if "col" not in location: return None
        return (state["file"], state["line"], location["col"])
    def full(location):
        if not location: return None, None
        if "spellingLoc" in location:
            spelling = bare(location["spellingLoc"])
            point = bare(location["expansionLoc"])
            if spelling and spelling[0] == SCRATCH: spelling = (SCRATCH, 0, 0)
            return spelling, point
        place = bare(location)
        return place, place
    references, declarations, implicit = [], {}, set()
    def walk(node):
        if not isinstance(node, dict): return
        if node.get("isImplicit") and "id" in node: implicit.add(node["id"])
        for key, value in node.items():
            if key == "loc":
                spelling, point = full(value)
                if "id" in node and spelling: declarations[node["id"]] = (spelling, point)
            elif key == "range":
                begin = full(value.get("begin"))
                full(value.get("end"))
                if node.get("kind") == "DeclRefExpr" and begin[0]:
                    references.append((begin, node["referencedDecl"]["id"]))
            elif key in ("inner", "array_filler"):
                for child in value: walk(child)
    walk(tree)
    for identifier in implicit: declarations.pop(identifier, None)
    return references, declarations, implicit


def binding_gate(binary, unit, work, details=None):
    text, origins = preprocess(binary, unit, "clang", work)
    dump = subprocess.run(["clang", *source_flags(unit), "-w", "-fsyntax-only", "-Xclang", "-ast-dump=json", unit["source"]],
                          cwd=unit["cwd"], capture_output=True)
    if dump.returncode:
        raise RuntimeError("clang: "+dump.stderr.decode(errors="replace")[-500:])
    tree = json.loads(dump.stdout)
    del dump
    references, declarations, implicit = clang_locations(tree)
    del tree
    data, _ = oracle.cesta(binary, "bind", unit, text, dialect="clang")
    # (use OFFSET ENTITY ROLE VISIBLE-DECLARATION ...): the declaration a reference
    # sees is the one Clang names
    uses = {int(o): (None if v == b"()" else int(v), role.decode())
            for o, _, role, v in re.findall(rb"^ \(use (\d+) (\d+) (\S+) (\(\)|\d+)", data, re.M)}
    by_place = collections.defaultdict(list)
    for offset, (spelling, point) in sorted(origins.items()):
        if offset in uses and uses[offset][1] == "reference":
            by_place[(spelling, point)].append(offset)
    taken = collections.Counter()
    counts = collections.Counter()
    for (spelling, point), declaration in references:
        offsets = by_place.get((spelling, point), [])
        index = taken[(spelling, point)]; taken[(spelling, point)] += 1
        if index >= len(offsets):
            counts["reference not located"] += 1; continue
        visible, role = uses[offsets[index]]
        if declaration in implicit:
            counts["reference to an implicit declaration, as Cesta binds it" if visible is None else "declaration position differs"] += 1
            continue
        if declaration not in declarations:
            counts["declaration without location"] += 1
            continue
        ours = origins.get(visible) if visible is not None else None
        if ours and ours == declarations[declaration] or (ours and ours[0] == declarations[declaration][0] != (SCRATCH, 0, 0)):
            counts["reference located and bound as Clang binds it"] += 1
        else:
            counts["declaration position differs"] += 1
            if details is not None and len(details) < 60:
                details.append({"reference": [spelling, point], "visible_offset": visible,
                                "ours": ours, "clang": declarations[declaration], "role": role})
    return counts


def qualify(unit, binary, output, inputs, prior=None):
    record = cache.context(unit)
    source = Path(unit["cwd"]) / unit["source"]
    record["source_sha256"] = sha(source.read_bytes())
    work = Path(tempfile.mkdtemp(dir=output / "work"))
    keep = False
    try:
        record["source_state"] = source_state(unit, work)
        if prior and cache.current(prior, inputs, unit) and passes(prior) and \
                prior.get("source_sha256") == record["source_sha256"] and \
                prior.get("source_state") == record["source_state"] and \
                sha(source.read_bytes()) == record["source_sha256"]:
            return prior, True
        try:
            record["census"], record["unmatched"] = census_gate(binary, unit, work)
        except Exception as error:
            record["census_error"] = str(error)[:500]
        try:
            record["binding_mismatches"] = []
            record["binding"] = binding_gate(binary, unit, work, record["binding_mismatches"])
        except Exception as error:
            record["binding_error"] = str(error)[:500]
        record["source_stable"] = (sha(source.read_bytes()) == record["source_sha256"] and
                                   source_state(unit, work) == record["source_state"])
        keep = not passes(record)
        if keep: record["failure_directory"] = str(work)
        return cache.receipt(record, inputs, unit), False
    except Exception as error:
        keep = True
        record["source_stable"] = False
        record["source_error"] = str(error)[:1000]
        record["failure_directory"] = str(work)
        return cache.receipt(record, inputs, unit), False
    finally:
        if not keep: shutil.rmtree(work, ignore_errors=True)


def passes(record):
    census, binding = record.get("census", {}), record.get("binding", {})
    return (bool(record.get("source_stable")) and "census_error" not in record and "binding_error" not in record and not census.get("unlocated")
            and not census.get("unclassified") and not binding.get("reference not located")
            and not binding.get("declaration position differs") and not binding.get("declaration without location"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/provenance-corpus")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--fresh", action="store_true", help="recheck instead of reusing current evidence")
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
                "scripts": {name: sha((ROOT / "tests" / name).read_bytes()) for name in
                            ("provenance_corpus.py", "preprocessor_corpus.py", "semantic_corpus.py",
                             "semantic_oracles.py", "corpus.py", "qualification_cache.py", "source_qualification.py")}}
    inputs = input_manifest()
    summary_path = output / "summary.json"
    previous = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    reusable = {cache.digest(cache.context(r)): r for r in previous.get("units", [])
                if not args.fresh and previous.get("inputs") == inputs}
    records, reused = [], 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(qualify, unit, binary, output, inputs,
                               reusable.get(cache.digest(cache.context(unit)))): unit for unit in units}
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
        for name, value in record.get("census", {}).items(): totals["warnings "+name] += value
        for name, value in record.get("binding", {}).items(): totals["references "+name] += value
    failures = [r for r in records if not passes(r)]
    stable = inputs == input_manifest()
    summary = {"inputs": inputs, "binary_sha256": sha(binary_bytes), "inputs_stable": stable,
               "reused_units": reused, "fresh_units": len(records)-reused, "totals": dict(totals),
               "all_gates_pass": stable and not failures and len(records) == len(units),
               "failures": [{k: r.get(k) for k in ("corpus", "unit", "census_error", "binding_error", "census", "binding",
                                                    "unmatched", "source_error", "failure_directory")} for r in failures], "units": records}
    (output / "summary.json").write_text(json.dumps(summary, indent=1)+"\n")
    for key in sorted(totals):
        print(f"{key}: {totals[key]}")
    for r in failures[:30]:
        print("FAILED", r["corpus"], r["unit"], json.dumps({k: r.get(k) for k in ("census_error", "binding_error", "census", "binding", "unmatched")})[:600])
    raise SystemExit(0 if summary["all_gates_pass"] else 1)


if __name__ == "__main__":
    main()
