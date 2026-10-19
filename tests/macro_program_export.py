"""Readable program export: exact contracts, identical objects, plain C."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-program-export-tests"
MACROS = ROOT / "macros"
CONTROLS = ROOT / "tests/modules/MacroControls.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


def use(module, theory):
    return f'(use-macros {json.dumps(str(module))} {theory})'


# Every library in one unit, with uses in the contexts an extraction could
# disturb: short-circuit and conditional operands, a loop condition that
# re-acquires its arguments on each test, an unevaluated operand, nested uses,
# and caller variables named like template locals.
PROGRAM = ('(cesta native-c/1 compact-c/2 (unit (include <stdint.h>) (include <stddef.h>) (include <stdlib.h>) '
           + use(MACROS / "Narrowing.module", "Checked") + ' '
           + use(MACROS / "Sizes.module", "Checked") + ' '
           + use(MACROS / "Worklists.module", "Growable") + ' '
           + use(MACROS / "Probes.module", "Switched") + ' '
           + use(CONTROLS, "Arithmetic") + ' '
           '(native "#ifndef STATS\\n#define STATS 0\\n#endif\\n'
           'uint64_t input(void); uint32_t *output(void); uint64_t countdown(void); uint64_t boom(void);\\n'
           'int first(void); int second(void); void sink(uint64_t);\\n'
           'void *grow(void *old, size_t bytes);\\ntypedef struct { uint64_t left, right; } Pair;") '
           '(fn int narrow64 ((uint64_t value) (uint32_t (* out))) (return (try-narrow uint64_t uint32_t value (* out)))) '
           '(fn int ordered (void) (return (try-narrow uint64_t uint32_t (call input) (* (call output))))) '
           '(fn int guarded ((int n) (uint32_t (* out))) (return (&& (> n 0) (try-narrow uint64_t uint32_t (call input) (* out))))) '
           '(fn int chosen ((int flag) (uint32_t (* out))) (return (?: flag (try-narrow uint64_t uint32_t (call input) (* out)) 7))) '
           '(fn int looped (void) (block (var uint8_t small 0) (var int iterations 0) '
           '(while (try-narrow uint64_t uint8_t (call countdown) small) (++ iterations)) (return iterations))) '
           '(fn size_t unevaluated ((uint32_t (* out))) (return (sizeof (try-narrow uint64_t uint32_t (call boom) (* out))))) '
           '(fn int nested ((uint32_t (* out))) (block (var uint64_t wide 0) '
           '(return (try-narrow uint64_t uint32_t (?: (try-narrow uint64_t uint64_t (call input) wide) wide 0) (* out))))) '
           '(fn int shadowed ((uint64_t accepted) (uint32_t (* converted))) (return (try-narrow uint64_t uint32_t accepted (* converted)))) '
           '(fn int pair_bytes ((uint32_t length) (size_t (* bytes))) (return (array-bytes Pair uint32_t size_t length (* bytes)))) '
           '(fn int doubled ((uint32_t capacity) (uint32_t (* next))) (return (doubled-capacity uint32_t 8u capacity (* next)))) '
           '(fn int push ((int (** buffer)) (size_t (* length)) (size_t (* capacity)) (int value)) '
           '(return (worklist-push int size_t grow (* buffer) (* length) (* capacity) value))) '
           '(fn void probed ((uint64_t n)) (block (var uint64_t hits 0) (if n (probe STATS (++ hits)) (probe STATS (+= hits 2))) '
           '(probe STATS (call sink hits)))) '
           '(fn int sum (void) (return (ordered-sum (call first) (call second)))) '
           '(native ' + json.dumps(r'''
static int order, input_calls, output_calls, boom_calls, sunk; static uint32_t cell; static uint64_t ticks = 3;
uint64_t input(void) { order = order * 10 + 1; ++input_calls; return 17; }
uint32_t *output(void) { order = order * 10 + 2; ++output_calls; return &cell; }
uint64_t countdown(void) { return ticks == 0 ? 300 : ticks--; }
uint64_t boom(void) { ++boom_calls; return 0; }
int first(void) { order = order * 10 + 3; return 1; }
int second(void) { order = order * 10 + 4; return 2; }
void sink(uint64_t delta) { (void)delta; ++sunk; }
void *grow(void *old, size_t bytes) { return realloc(old, bytes); }
int main(void) {
    uint32_t out = 99; size_t bytes = 0;
    if (!narrow64(UINT32_MAX, &out) || out != UINT32_MAX) return 1;
    out = 99; if (narrow64((uint64_t)UINT32_MAX + 1, &out) || out != 99) return 2;
    order = 0; if (!ordered() || order != 12 || cell != 17) return 3;
    input_calls = 0; if (guarded(0, &out) || input_calls != 0) return 4;
    if (!guarded(1, &out) || input_calls != 1) return 5;
    input_calls = 0; if (chosen(0, &out) != 7 || input_calls != 0) return 6;
    if (looped() != 3) return 7;
    if (unevaluated(&out) != sizeof(int) || boom_calls != 0) return 8;
    if (!nested(&out) || out != 17) return 9;
    out = 0; if (!shadowed(5, &out) || out != 5) return 10;
    if (!pair_bytes(4u, &bytes) || bytes != 4 * sizeof(Pair)) return 11;
    uint32_t next = 0; if (!doubled(0u, &next) || next != 8) return 12;
    int *items = NULL; size_t length = 0, capacity = 0;
    for (int i = 0; i < 20; ++i) if (!push(&items, &length, &capacity, i)) return 13;
    if (length != 20 || items[19] != 19) return 14;
    free(items);
    probed(1); if (STATS ? sunk != 1 : sunk != 0) return 15;
    order = 0; if (sum() != 3 || order != 34) return 16;
    return 0;
}
''') + ')))\n')

REFUSALS = (
    ("deferred", use(CONTROLS, "Arithmetic") + ' (fn int f ((int c)) (return (choose-lazy c 1 2)))', b"deferred or repeated"),
    ("repeated", use(CONTROLS, "Arithmetic") + ' (fn int f ((int c)) (return (repeat-twice c)))', b"deferred or repeated"),
    ("c11-statement", use(CONTROLS, "Arithmetic") + ' (fn void f ((int c)) (when c (return)))', b"gnu11 profile"),
    ("positional-copy", use(MACROS / "Probes.module", "Switched") +
     ' (fn void f ((int c)) (probe __LINE__ (++ c)))', b"preprocessor counter"),
)


def main():
    if WORK.exists(): shutil.rmtree(WORK)
    for side in ("exported", "expanded"):
        (WORK / side).mkdir(parents=True)
    source = WORK / "expanded/program.cesta"; source.write_text(PROGRAM)
    records = []
    for compiler in ("gcc", "clang"):
        exported = WORK / "exported/program.c"
        run([BINARY, "export-c", "--cc", compiler, source, "-o", exported])
        again = WORK / f"{compiler}-again.c"
        run([BINARY, "export-c", "--cc", compiler, source, "-o", again])
        assert exported.read_bytes() == again.read_bytes(), compiler + ": regeneration differs"
        text = exported.read_text()
        assert "cesta_macro_" not in text and "MacroNativeArgument" not in text, "generated names leaked"
        assert "__VA_ARGS__" not in text and "#pragma" not in text, "helper machinery leaked"
        objects = []
        for optimization in ("-O0", "-O2"):
            # The program deliberately places a use with effects under sizeof,
            # which clang reports for any C; both sides compile alike.
            flags = ["-std=gnu11", optimization, "-g0", "-Wall", "-Wextra", "-Werror"] + \
                (["-Wno-unevaluated-expression"] if compiler == "clang" else [])
            mine = WORK / f"{compiler}{optimization}-exported.o"; theirs = WORK / f"{compiler}{optimization}-expanded.o"
            run([compiler, *flags, "-c", "program.c", "-o", mine], WORK / "exported")
            run([BINARY, "cc", "--cc", compiler, *flags, "-c", "program.cesta", "-o", theirs], WORK / "expanded")
            assert mine.read_bytes() == theirs.read_bytes(), f"{compiler} {optimization}: exported object differs"
            for stats in ("0", "1"):
                executable = WORK / f"{compiler}{optimization}-stats{stats}"
                run([compiler, *flags, "-DSTATS=" + stats, "program.c", "-o", executable], WORK / "exported")
                run([executable])
            objects.append({"optimization": optimization, "sha256": hashlib.sha256(mine.read_bytes()).hexdigest()})
        records.append({"compiler": compiler, "objects_identical_to_expansion": objects})
        print(compiler + ": readable program export regenerates identically, runs, and matches the expansion's objects",
              flush=True)
    for name, body, diagnostic in REFUSALS:
        path = WORK / (name + ".cesta")
        path.write_text('(cesta native-c/1 compact-c/2 (unit ' + body + '))\n')
        result = run([BINARY, "export-c", "--cc", "gcc", path, "-o", WORK / (name + ".c")], success=False)
        assert diagnostic in result.stderr, (name, result.stderr)
    (WORK / "summary.json").write_text(json.dumps({"records": records, "refusals": len(REFUSALS),
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(f"program export: identical objects at O0 and O2 on both compilers; {len(REFUSALS)} refusals", flush=True)


if __name__ == "__main__":
    main()
