"""Readable header export: plain C, regeneration, behaviour, cost and refusals."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
FIXTURES = ROOT / "tests/export"
WORK = ROOT / "build/macro-export-tests"
SPECS = ("sizes", "narrowing", "worklists", "probes")
FLAGS = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pedantic", "-g0"]


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


def export(spec, output):
    return run([BINARY, "export-header", spec, "-o", output])


# Behaviour of every exported entity, mirroring the library tests' boundaries.
PROGRAM = r'''
#include <limits.h>
#include <stdlib.h>
#include "sizes.h"
#include "narrowing.h"
#include "worklists.h"
#include "probes.h"
#ifndef STATS
#define STATS 0
#endif
static int sunk; static uint64_t sunk_delta;
static void sink(uint64_t delta) { ++sunk; sunk_delta = delta; }
static void *grow(void *old, size_t bytes) { return realloc(old, bytes); }
static void *refuse(void *old, size_t bytes) { (void)old; (void)bytes; return NULL; }
int main(void) {
    size_t out = 99; uint32_t small = 99;
    if (!ct_pair_bytes_u32(UINT32_MAX, &out) || out != (size_t)UINT32_MAX * sizeof(Pair)) return 1;
    out = 99; if (!ct_pair_bytes_u64(SIZE_MAX / sizeof(Pair), &out)) return 2;
    out = 99; if (ct_pair_bytes_u64(SIZE_MAX / sizeof(Pair) + 1, &out) || out != 99) return 3;
    small = 99; if (!ct_doubled_capacity_u32(8u, 0u, &small) || small != 8) return 4;
    small = 99; if (!ct_doubled_capacity_u32(8u, UINT32_MAX / 2, &small) || small != UINT32_MAX - 1) return 5;
    small = 99; if (ct_doubled_capacity_u32(8u, UINT32_MAX / 2 + 1, &small) || small != 99) return 6;
    small = 99; if (!ct_narrow_u64_u32(UINT32_MAX, &small) || small != UINT32_MAX) return 7;
    small = 99; if (ct_narrow_u64_u32((uint64_t)UINT32_MAX + 1, &small) || small != 99) return 8;
    int *items = NULL; size_t length = 0, capacity = 0;
    for (int i = 0; i < 100; ++i) if (!ct_worklist_push_int(grow, &items, &length, &capacity, i)) return 9;
    if (length != 100 || capacity < 100 || items[99] != 99 || items[0] != 0) return 10;
    int *before = items; size_t full = capacity; length = capacity;
    if (ct_worklist_push_int(refuse, &items, &length, &capacity, 7) || items != before || capacity != full) return 11;
    free(items);
    uint64_t hits = 0;
    for (int i = 0; i < 5; ++i) { if (i == 3) break; if (i) ct_probe(STATS, ++hits); else ct_probe(STATS, hits += 2); }
    ct_probe(STATS, sink(hits));
    if (STATS ? (hits != 4 || sunk != 1 || sunk_delta != 4) : (hits != 0 || sunk != 0)) return 12;
    return 0;
}
'''

# Callers' macros named like the header's parameters and locals keep their
# meaning outside it; inside, the header's names are its own.
SHADOWED = r'''
#define bytes broken_bytes
#define out broken_out
#define capacity broken_capacity
#define next broken_next
#include "sizes.h"
#include "sizes.h"
int other(void);
int main(void) {
    int broken_out = 3; size_t place = 0;
    if (!ct_pair_bytes_u32(4u, &place) || place != 4 * sizeof(Pair)) return 1;
    return out == 3 && other() == 0 ? 0 : 2;
}
'''
OTHER = '#include "sizes.h"\nint other(void) { size_t b = 0; return ct_pair_bytes_u32(1u, &b) && b == sizeof(Pair) ? 0 : 1; }\n'

NEGATIVES = (
    ("unknown-constructor", '(function ct_x Missing)', b"unknown constructor"),
    ("missing-type", '(function ct_x ArrayBytes (element Pair) (count uint32_t))', b"missing type parameter size"),
    ("unknown-type", '(function ct_x DoubledCapacity (count uint32_t) (width int))', b"unknown type parameter"),
    ("duplicate-name", '(function ct_x DoubledCapacity (count uint32_t)) (function ct_x DoubledCapacity (count uint64_t))',
     b"duplicate exported name"),
    ("invalid-name", '(function 9x DoubledCapacity (count uint32_t))', b"export requires a C name"),
    ("value-as-type", '(function ct_x ArrayBytes (element Pair) (count uint32_t) (size size_t) (length int))',
     b"is not a type parameter"),
)


def main():
    if WORK.exists(): shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    for name in ("pair.h",) + tuple(s + ".export" for s in SPECS):
        shutil.copy(FIXTURES / name, WORK / name)
    for spec in SPECS:
        text = (WORK / (spec + ".export")).read_text().replace('"../../macros/', '"' + str(ROOT / "macros") + '/')
        (WORK / (spec + ".export")).write_text(text)
    headers = {}
    for spec in SPECS:
        first = WORK / (spec + ".h"); second = WORK / (spec + ".again.h")
        export(spec + ".export", first); export(spec + ".export", second)
        assert first.read_bytes() == second.read_bytes(), spec + ": regeneration differs"
        text = first.read_text(); headers[spec] = hashlib.sha256(first.read_bytes()).hexdigest()
        assert "cesta_macro_" not in text, spec + ": generated helper names leaked"
        assert str(ROOT) not in text and "/home/" not in text, spec + ": workstation path in header"
        assert "_Generic" not in text and "__VA_ARGS__" not in text, spec + ": use-site admission leaked"
        assert all(len(line) <= 160 for line in text.splitlines()), spec + ": overlong line"
    for spec, needed in (("sizes", ("static inline int ct_pair_bytes_u32", "__builtin_mul_overflow")),
                         ("probes", ("#define ct_probe(enabled, effect)",)),
                         ("narrowing", ("static inline int ct_narrow_u64_u32",)),
                         ("worklists", ("static inline int ct_worklist_push_int",))):
        text = (WORK / (spec + ".h")).read_text()
        for fragment in needed:
            assert fragment in text, f"{spec}: expected {fragment!r}\n{text}"
    (WORK / "program.c").write_text(PROGRAM)
    (WORK / "shadowed.c").write_text(SHADOWED); (WORK / "other.c").write_text(OTHER)
    records = []
    for compiler in ("gcc", "clang"):
        for header in SPECS:
            unit = WORK / (header + "-alone.c")
            unit.write_text(f'#include "{header}.h"\nint main(void) {{ return 0; }}\n')
            run([compiler, *FLAGS, "-fsyntax-only", unit])
        for stats in ("0", "1"):
            for optimization in ("-O0", "-O2", "-O3"):
                executable = WORK / f"{compiler}{optimization}-stats{stats}"
                run([compiler, *FLAGS, optimization, "-DSTATS=" + stats, "program.c", "-o", executable])
                run([executable])
        run([compiler, *FLAGS, "-O2", "shadowed.c", "other.c", "-o", WORK / (compiler + "-shadowed")])
        run([WORK / (compiler + "-shadowed")])
        records.append({"compiler": compiler, "standalone_headers": len(SPECS), "behaviour_profiles": 6,
                        "caller_macro_shadowing": True, "two_translation_units": True})
        print(compiler + ": exported headers standalone, behaviour, shadowing and repeated inclusion passed", flush=True)
    for name, body, diagnostic in NEGATIVES:
        spec = WORK / (name + ".export")
        spec.write_text('(cesta-export/1 (header CT_N_H) (include "pair.h") (use ' +
                        json.dumps(str(ROOT / "macros/Sizes.module")) + ' Checked) ' + body + ')\n')
        result = run([BINARY, "export-header", spec, "-o", WORK / (name + ".h")], success=False)
        assert diagnostic in result.stderr, (name, result.stderr)
        assert not (WORK / (name + ".h")).exists(), name + ": refused export wrote a header"
    probe_as_function = WORK / "probe-function.export"
    probe_as_function.write_text('(cesta-export/1 (header CT_P_H) (use ' + json.dumps(str(ROOT / "macros/Probes.module")) +
                                 ' Switched) (function ct_probe Probe))\n')
    result = run([BINARY, "export-header", probe_as_function, "-o", WORK / "probe-function.h"], success=False)
    assert b"exported as a macro" in result.stderr, result.stderr
    # Textual substitution cannot preserve acquisition of evaluated arguments.
    controls = ROOT / "tests/modules/MacroControls.module"
    evaluated_refusals = ("Twice", "Ordered", "Increment", "Lazy", "Repeated")
    for constructor in evaluated_refusals:
        spec = WORK / (constructor + ".export")
        spec.write_text('(cesta-export/1 (header CT_ONCE_H) (use ' + json.dumps(str(controls)) +
                        ' Arithmetic) (macro ct_bad ' + constructor + '))\n')
        output = WORK / (constructor + ".h")
        result = run([BINARY, "export-header", spec, "-o", output], success=False)
        assert b"has evaluated arguments" in result.stderr, result.stderr
        assert not output.exists(), constructor + ": refused macro wrote a header"
    once_spec = WORK / "once.export"
    once_spec.write_text('(cesta-export/1 (header CT_ONCE_H) (use ' + json.dumps(str(controls)) +
                         ' Arithmetic) (function ct_twice Twice))\n')
    export(once_spec, WORK / "once.h")
    (WORK / "once.c").write_text('#include "once.h"\n'
        'static int calls; static int tick(void) { ++calls; return 3; }\n'
        'int main(void) { int n = ct_twice(tick()); return n == 6 && calls == 1 ? 0 : 1; }\n')
    for compiler in ("gcc", "clang"):
        for optimization in ("-O0", "-O2"):
            executable = WORK / (compiler + optimization + "-once")
            run([compiler, *FLAGS, optimization, "once.c", "-o", executable])
            run([executable])
    refusals = len(NEGATIVES) + 1 + len(evaluated_refusals)
    (WORK / "summary.json").write_text(json.dumps({"records": records, "headers": headers,
        "negative_controls": refusals, "evaluated_macro_refusals": list(evaluated_refusals),
        "function_argument_evaluated_once": True,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(f"header export: {len(SPECS)} specifications regenerate identically; {refusals} refusals; "
          "function arguments evaluated once", flush=True)


if __name__ == "__main__":
    main()
