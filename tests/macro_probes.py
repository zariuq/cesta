"""Switched probes: checked in every build, no code when the switch is off."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-probes-tests"
MODULE = ROOT / "macros/Probes.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


PRELUDE = '#include <stdint.h>\n#include <stddef.h>\n' \
    '#ifndef STATS\n#define STATS 0\n#endif\n' \
    'void sink(int counter, uint64_t delta);\n'


def document(*forms, module=MODULE):
    return '(cesta native-c/1 compact-c/2 (unit (native ' + json.dumps(PRELUDE) + ') (use-macros ' + \
        json.dumps(str(module)) + ' Switched) ' + " ".join(forms) + '))\n'


# The counter exists only for the probes; with the switch off it must neither
# warn as set-but-unused nor cost anything.
COUNT = ('(fn size_t count_live (((const unsigned char) (* s)) (size_t n)) '
         '(block (var size_t live 0) (var uint64_t trimmed 0) '
         '(for (var size_t i 0) (< i n) (++ i) '
         '(if ([] s i) (++ live) (probe STATS (++ trimmed)))) '
         '(probe STATS (call sink 3 trimmed)) (return live)))')

RUNTIME = r'''
static int calls, last_counter; static uint64_t last_delta;
void sink(int counter, uint64_t delta) { ++calls; last_counter=counter; last_delta=delta; }
int main(void) {
    static const unsigned char bytes[] = {1, 0, 2, 0, 0, 3};
    if (count_live(bytes, sizeof bytes) != 3) return 1;
    if (STATS) { if (calls!=1 || last_counter!=3 || last_delta!=3) return 2; }
    else if (calls!=0) return 3;
    return 0;
}
'''

HANDWRITTEN = PRELUDE + (
    '#define PROBE(E,x) { _Static_assert(__builtin_constant_p(E), '
    '"probe switch must be a constant expression"); (E) ? (void)(x) : (void)0; }\n'
    'size_t count_live(const unsigned char *s, size_t n) {\n'
    '  size_t live = 0; uint64_t trimmed = 0;\n'
    '  for (size_t i = 0; i < n; ++i) if (s[i]) ++live; else PROBE(STATS, ++trimmed)\n'
    '  PROBE(STATS, sink(3, trimmed))\n'
    '  return live;\n}\n')

WITHOUT = PRELUDE + (
    'size_t count_live(const unsigned char *s, size_t n) {\n'
    '  size_t live = 0;\n'
    '  for (size_t i = 0; i < n; ++i) if (s[i]) ++live;\n'
    '  return live;\n}\n')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "program.cesta"
    source.write_text(document(COUNT, '(native ' + json.dumps(RUNTIME) + ')'))
    handwritten = WORK / "handwritten"; handwritten.mkdir(exist_ok=True)
    (handwritten / "program.c").write_text(HANDWRITTEN + RUNTIME)
    bare = WORK / "bare"; bare.mkdir(exist_ok=True)
    (bare / "count.cesta").write_text(document(COUNT))
    without = bare / "without"; without.mkdir(exist_ok=True)
    (without / "count.c").write_text(WITHOUT)
    records = []
    for compiler in ("gcc", "clang"):
        work = WORK / compiler; work.mkdir(exist_ok=True)
        objects = []
        for switch in ("0", "1"):
            for optimization in ("-O0", "-O2"):
                flags = ["-std=gnu11", optimization, "-g0", "-Wall", "-Wextra", "-Werror", "-DSTATS=" + switch]
                stem = optimization + "-stats" + switch
                actual = work / (stem + ".o"); reference = handwritten / (compiler + stem + ".o")
                run([BINARY, "cc", "--cc", compiler, *flags, "-c", source, "-o", actual], work)
                run([compiler, *flags, "-c", "program.c", "-o", reference], handwritten)
                assert actual.read_bytes() == reference.read_bytes(), compiler + stem + ": object differs"
                executable = work / (stem + "-program")
                run([compiler, actual, "-o", executable], work); run([executable], work)
                objects.append({"stats": switch, "optimization": optimization,
                                "sha256": hashlib.sha256(actual.read_bytes()).hexdigest()})
        for switch in ("0", "1"):
            for optimization in ("-O0", "-O2"):
                flags = ["-std=gnu11", optimization, "-g0", "-Wall", "-Wextra", "-Werror", "-DSTATS=" + switch]
                unit = bare / (compiler + optimization + "-stats" + switch + ".o")
                run([BINARY, "cc", "--cc", compiler, *flags, "-c", "count.cesta", "-o", unit], bare)
                symbols = run(["nm", unit], bare).stdout
                assert (b" U sink" in symbols) == (switch == "1"), compiler + optimization + ": sink reference"
        # With the switch off, optimized code equals the program written without probes.
        flags = ["-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror", "-DSTATS=0"]
        off = bare / (compiler + "-off.o"); plain = without / (compiler + ".o")
        run([BINARY, "cc", "--cc", compiler, *flags, "-c", "count.cesta", "-o", off], bare)
        run([compiler, *flags, "-c", "count.c", "-o", plain], without)
        assert off.read_bytes() == plain.read_bytes(), compiler + ": a disabled probe left code"
        exported = work / "exported.c"
        exported.write_bytes(run([BINARY, "expand", "--cc", compiler, source], work).stdout)
        for switch in ("0", "1"):
            run([compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-DSTATS=" + switch,
                 exported, "-o", work / ("exported" + switch)], work); run([work / ("exported" + switch)], work)
        negatives = (
            ("variable-switch", '(fn int main (void) (var int enabled 0) (var int hits 0) '
             '(probe enabled (++ hits)) (return hits))', b"probe switch must be a constant expression"),
            ("ill-typed-when-off", '(native "struct box {int v;};") (fn int main (void) (var (struct box) b) '
             '(probe STATS (call sink 3 b)) (return 0))', None),
            ("undeclared-when-off", '(fn int main (void) (probe STATS (call missing_sink 3 1)) (return 0))', None),
        )
        for name, body, diagnostic in negatives:
            path = work / (name + ".cesta"); path.write_text(document(body))
            result = run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-Werror", "-DSTATS=0", "-c", path,
                          "-o", work / (name + ".o")], work, success=False)
            if diagnostic: assert diagnostic in result.stderr, result.stderr
        records.append({"compiler": compiler, "objects": objects, "disabled_equals_unprobed": True,
                        "checked_when_off": True, "exported_native": True, "negative_controls": len(negatives)})
        print(compiler + ": probes on/off, zero-cost off, checking when off, export and identical objects passed",
              flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        "module_sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
