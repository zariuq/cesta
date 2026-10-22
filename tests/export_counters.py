"""Counter tables: one structured definition yields the enumeration and spellings."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
FIXTURES = ROOT / "tests/export"
FLAGS = ["-std=c11", "-g0", "-Wall", "-Wextra", "-Werror", "-pedantic"]
USE = '''const char *const *table_names(void) { return @NAMES@; }
int table_count(void) { return @COUNT@; }
const char *table_name(@TYPE@ c) { return c < @COUNT@ ? @NAMES@[c] : "unknown-counter"; }
'''


def run(arguments, cwd, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


def use(type_name, prefix, names):
    return USE.replace("@NAMES@", names).replace("@COUNT@", prefix + "COUNT").replace("@TYPE@", type_name)


def export_pair(work, data_name):
    for spec, header in (("counters", "counters.h"), ("counter-names", "counter-names.h")):
        first = run([BINARY, "export-header", f"{spec}.export", "-o", header], work)
        again = work / (header + ".again")
        run([BINARY, "export-header", f"{spec}.export", "-o", again], work)
        assert (work / header).read_bytes() == again.read_bytes(), spec + ": regeneration differs"
        assert first.returncode == 0


def compare(work, reference_source, type_name, prefix, names, rows):
    """Objects from generated headers equal those from the reference source."""
    (work / "ref").mkdir(exist_ok=True); (work / "gen").mkdir(exist_ok=True)
    (work / "ref/table.c").write_text(reference_source + "\n" + use(type_name, prefix, names))
    asserts = "".join(f'_Static_assert({prefix}{name} == {value}, "{name}");\n' for name, value in rows)
    (work / "gen/table.c").write_text('#include "../counters.h"\n#include "../counter-names.h"\n' + asserts +
                                      use(type_name, prefix, names))
    results = []
    for compiler in ("gcc", "clang"):
        for optimization in ("-O0", "-O2"):
            outputs = []
            for side in ("ref", "gen"):
                output = work / f"{side}-{compiler}{optimization}.o"
                run([compiler, *FLAGS, optimization, "-c", "table.c", "-o", output], work / side)
                outputs.append(output.read_bytes())
            assert outputs[0] == outputs[1], f"{compiler} {optimization}: generated table object differs"
            results.append({"compiler": compiler, "optimization": optimization,
                            "sha256": hashlib.sha256(outputs[1]).hexdigest()})
    return results


NEGATIVES = (
    ("gap", '(0 A "a") (2 B "b")', b"dense table expects ID 1"),
    ("order", '(1 A "a") (0 B "b")', b"dense table expects ID 0"),
    ("duplicate-name", '(0 A "a") (1 A "b")', b"duplicate row name"),
    ("duplicate-spelling", '(0 A "a") (1 B "a")', b"duplicate row spelling"),
    ("identifier", '(0 9A "a")', b"row name must be a C identifier"),
    ("count-collision", '(0 COUNT "a")', b"row name collides with the count"),
    ("spelling", '(0 A "a\\\\b")', b"printable ASCII"),
    ("leading-zero", '(00 A "a")', b"decimal natural number"),
)


def fixture(work):
    for name in ("counters.data", "counters.export", "counter-names.export"):
        shutil.copy(FIXTURES / name, work / name)
    export_pair(work, "counters.data")
    data = (work / "counters.data").read_text()
    rows = [(n, int(i)) for i, n in re.findall(r"\((\d+) ([A-Z0-9_]+) ", data)]
    results = compare(work, (FIXTURES / "counters-reference.c").read_text(), "ProbeCounter", "PROBE_COUNTER_",
                      "PROBE_COUNTER_NAMES", rows)
    for name, body, diagnostic in NEGATIVES:
        (work / f"{name}.data").write_text("(cesta-counters/1 (enum T) (prefix P_) (count COUNT) (names N) "
                                           f"(profile dense) (rows {body}))\n")
        (work / f"{name}.export").write_text(f'(cesta-export/1 (header H) (counters "{name}.data"))\n')
        result = run([BINARY, "export-header", f"{name}.export", "-o", f"{name}.h"], work, success=False)
        assert diagnostic in result.stderr, (name, result.stderr)
        assert not (work / f"{name}.h").exists(), name + ": refused table wrote a header"
    (work / "sparse.data").write_text("(cesta-counters/1 (enum T) (prefix P_) (count COUNT) (names N) "
                                      '(profile sparse) (rows (0 A "a")))\n')
    (work / "sparse.export").write_text('(cesta-export/1 (header H) (counters "sparse.data"))\n')
    result = run([BINARY, "export-header", "sparse.export", "-o", "sparse.h"], work, success=False)
    assert b"dense profile" in result.stderr, result.stderr
    print(f"counter tables: fixture of {len(rows)} rows regenerates identically and matches its reference "
          f"objects; {len(NEGATIVES) + 1} refusals", flush=True)
    literals = spelling_literals(work / "spellings")
    return {"rows": len(rows), "objects": results, "negative_controls": len(NEGATIVES) + 1,
            "spelling_literals": literals}


def spelling_literals(work):
    """C11 translation preserves all admitted printable bytes, including trigraphs."""
    work.mkdir()
    spellings = ["??" + c for c in "=/'()!<>-"] + ["???=", "?",
        "".join(chr(c) for c in range(32, 127) if chr(c) not in '"\\')]
    rows = " ".join(f"({i} S{i} {json.dumps(s)})" for i, s in enumerate(spellings))
    (work / "counters.data").write_text('(cesta-counters/1 (enum T) (prefix P_) (count COUNT) '
                                        f'(names N) (profile dense) (rows {rows}))\n')
    for spec, entry in (("counters", "counters"), ("counter-names", "counter-names")):
        (work / (spec + ".export")).write_text(
            f'(cesta-export/1 (header H_{spec.replace("-", "_").upper()}) ({entry} "counters.data"))\n')
    export_pair(work, "counters.data")
    expected = []
    for i, spelling in enumerate(spellings):
        values = ", ".join(map(str, spelling.encode("ascii") + b"\0"))
        expected.append(f"static const unsigned char expected{i}[] = {{{values}}};")
    checks = "\n".join(f'    if (strcmp(N[{i}], (const char *)expected{i})) return {i + 1};'
                       for i in range(len(spellings)))
    # Byte arrays keep the reference independent of trigraph translation.
    (work / "literals.c").write_text('#include <string.h>\n#include "counters.h"\n'
        '#include "counter-names.h"\n' + "\n".join(expected) + '\nint main(void) {\n' + checks +
        '\n    return 0;\n}\n')
    for compiler in ("gcc", "clang"):
        for standard in ("c11", "gnu11"):
            for optimization in ("-O0", "-O2"):
                executable = work / (compiler + "-" + standard + optimization)
                run([compiler, *FLAGS, "-std=" + standard, optimization, "literals.c", "-o", executable], work)
                run([executable], work)
    print(f"counter spellings: {len(spellings)} literals, all nine trigraphs and printable ASCII "
          "preserved under GCC and Clang in c11 and gnu11", flush=True)
    return {"literals": len(spellings), "compiler_profiles": 8, "all_trigraphs": True}


LEAN = ROOT.parent / "Mettapedia/lean/mettapedia"


def counter_code(s):
    """The number ExportCounterTables.encode gives a string: base-256 digits, shifted by one."""
    n = 0
    for c in reversed(s): n = n * 256 + (ord(c) + 1)
    return n


def kernel_admission(work, data_path, chunk=100, lean_memory_mb=None):
    """Decide the dense profile of the actual rows in the Lean kernel, a chunk of rows per declaration."""
    from export_admission import atom, lean_command
    from oracles.pro_compact_cesta import parse
    data, = parse(data_path.read_bytes())
    fields = {str(x[0]): x for x in data[1:6]}
    rows = [(int(str(r[0])), str(r[1]), r[2].data.decode("utf-8")) for r in data[6][1:]]
    enum, prefix, count, names = (str(fields[k][1]) for k in ("enum", "prefix", "count", "names"))
    chunks = [rows[i:i + chunk] for i in range(0, len(rows), chunk)] or [[]]
    lines = ["import Mettapedia.GSLT.LanguageDef.ExportCounterTables",
             "open Mettapedia.GSLT.LanguageDef.MacroExportPlan Mettapedia.GSLT.LanguageDef.ExportCounterTables",
             "set_option maxRecDepth 100000", "namespace CestaCounterAdmission",
             f"def table : Atom := {atom(data)}"]
    for j, part in enumerate(chunks):
        lines.append(f"def rows{j} : List Row := [" +
                     ", ".join(f"⟨{i}, {json.dumps(n)}, {json.dumps(s)}⟩" for i, n, s in part) + "]")
        lines.append(f"theorem chunk{j} : rowsOk rows{j} [{', '.join(str(counter_code(n)) for _, n, _ in part)}] "
                     f"[{', '.join(str(counter_code(s)) for _, _, s in part)}] = true := by decide +kernel")
    every = " ++ (".join(f"rows{j}" for j in range(len(chunks))) + ")" * (len(chunks) - 1)
    lines.append(f"def decoded : Table := ⟨{json.dumps(enum)}, {json.dumps(prefix)}, {json.dumps(count)}, "
                 f"{json.dumps(names)}, {every}⟩")
    lines.append("theorem decodes : decodeTable table = some decoded := rfl")
    codes = [counter_code(count)] + [counter_code(n) for _, n, _ in rows]
    lines.append(f"theorem distinctNames : allDistinct [{', '.join(map(str, codes))}] .leaf = true := by decide +kernel")
    lines.append(f"theorem distinctSpellings : allDistinct [{', '.join(str(counter_code(s)) for _, _, s in rows)}] "
                 ".leaf = true := by decide +kernel")
    lines.append(f"theorem tableChecks : tableOk decoded {counter_code(count)} = true := by decide +kernel")
    nest = f"chunk{len(chunks) - 1}"
    for j in range(len(chunks) - 2, -1, -1): nest = f"rowsOk_append chunk{j} ({nest})"
    lines.append(f"theorem dense : Dense decoded := dense_of_checked decoded _ _ _ ({nest}) distinctNames "
                 "distinctSpellings tableChecks")
    lines.append("end CestaCounterAdmission")
    lines.append("#print axioms CestaCounterAdmission.dense")
    source = work / "admission.lean"; source.write_text("\n".join(lines) + "\n")
    result = subprocess.run(lean_command(source, lean_memory_mb), cwd=LEAN, capture_output=True)
    (work / "admission.log").write_bytes(result.stdout + result.stderr)
    output = result.stdout.decode(errors="replace")
    if result.returncode or "error" in output or "warning:" in output or "sorry" in output:
        raise RuntimeError(output[-8000:])
    found = output.split("depends on axioms:")[1].split("]")[0].strip(" [\n")
    assert {x.strip(" ,\n") for x in found.split(",")} <= {"propext", "Classical.choice", "Quot.sound"}, found
    return {"rows": len(rows), "chunks": len(chunks)}


def project_declarations(path):
    """Read inline declarations and a project's checked-in generated headers."""
    text = path.read_text()
    for include in re.findall(r'^\s*#include\s+"(generated/[^"\n]+)"', text, re.M):
        header = (path.parent / include).resolve()
        if not header.is_relative_to(path.parent.resolve()):
            raise AssertionError("generated counter include leaves its source directory")
        text += "\n" + header.read_text()
    return text


def cetta_fragments(source):
    """The actual C declarations, independently of authored table data."""
    header = project_declarations(source / "stats.h")
    body = project_declarations(source / "stats.c")
    end = header.index("} CettaRuntimeCounter;") + len("} CettaRuntimeCounter;")
    start = header.rfind("typedef enum {", 0, end)
    assert start >= 0, "counter enumeration has no declaration"
    enum = header[start:end]
    start = body.index("static const char *const CETTA_RUNTIME_COUNTER_NAMES[")
    names = body[start:body.index("};", start) + 2]
    return enum, names


def cetta(work, source, lean_memory_mb=None):
    """The actual CeTTa runtime counters, extracted independently of the exporter."""
    enum, names = cetta_fragments(source)
    rows = [(n, int(v)) for n, v in re.findall(r"CETTA_RUNTIME_COUNTER_([A-Z0-9_]+)\s*=\s*(\d+)", enum)]
    assert rows[-1][0] == "COUNT"; rows = rows[:-1]
    spellings = re.findall(r'"([^"]*)"', names)
    assert len(spellings) == len(rows), "CeTTa names and enumerators disagree"
    lines = ["(cesta-counters/1", "  (enum CettaRuntimeCounter)", "  (prefix CETTA_RUNTIME_COUNTER_)", "  (count COUNT)",
             "  (names CETTA_RUNTIME_COUNTER_NAMES)", "  (profile dense)", "  (rows"]
    lines += [f'    ({value} {name} "{spelling}")' for (name, value), spelling in zip(rows, spellings)]
    lines[-1] += "))"
    (work / "counters.data").write_text("\n".join(lines) + "\n")
    (work / "counters.export").write_text('(cesta-export/1 (header CETTA_COUNTERS_H) (counters "counters.data"))\n')
    (work / "counter-names.export").write_text('(cesta-export/1 (header CETTA_COUNTER_NAMES_H) (include "counters.h") '
                                               '(counter-names "counters.data"))\n')
    export_pair(work, "counters.data")
    results = compare(work, enum + "\n" + names, "CettaRuntimeCounter", "CETTA_RUNTIME_COUNTER_",
                      "CETTA_RUNTIME_COUNTER_NAMES", rows)
    admitted = kernel_admission(work, work / "counters.data", lean_memory_mb=lean_memory_mb)
    print(f"counter tables: CeTTa's {len(rows)} runtime counters regenerate identically, match its objects, and "
          f"have the dense profile by the Lean kernel ({admitted['chunks']} chunks)", flush=True)
    return {"rows": len(rows), "objects": results, "kernel_admission": admitted,
            "source_sha256": hashlib.sha256((enum + names).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cetta", type=Path, help="CeTTa src directory whose counter table is compared")
    parser.add_argument("--lean-memory-mb", type=int, help="optional Lean memory limit in MiB")
    options = parser.parse_args()
    if options.lean_memory_mb is not None and options.lean_memory_mb <= 0:
        parser.error("--lean-memory-mb must be positive")
    work = ROOT / ("build/export-counters-cetta" if options.cetta else "build/export-counters")
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    if options.cetta:
        if not (options.cetta / "stats.h").exists():
            raise SystemExit(f"CeTTa source not found at {options.cetta}")
        summary = cetta(work, options.cetta.resolve(), options.lean_memory_mb)
    else:
        summary = fixture(work)
    summary["binary_sha256"] = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
