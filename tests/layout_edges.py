"""Layout generation at word, prefix, parameter and exported-name boundaries."""

import argparse
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/layout-edges"
MODULE = ROOT / "tests/layouts/Edges.module"


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=ROOT, capture_output=True, text=True,
                          check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    binary = ROOT / "build/cesta"
    if args.sanitize:
        commands = [shlex.split(line) for line in run(["make", "-Bn", "build/cesta"]).stdout.splitlines()]
        command, = [line for line in commands if "-o" in line and line[line.index("-o") + 1] == "build/cesta"]
        command = [a for a in command if a not in ("-O2", "-g")]
        command[1:1] = ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        binary = WORK / "cesta-sanitized"
        command[command.index("-o") + 1] = str(binary)
        run(command)

    for theory in ("Widths", "Many", "Deep"):
        run([binary, "layout", "check", MODULE, theory])
        run([binary, "layout", "functions", MODULE, theory])
        run([binary, "layout", "header", MODULE, theory, "-o", WORK / (theory + ".h")])

    zeroes = ", ".join(["37"] + ["0"] * 64)
    bodies = {
        "Widths": """uint64_t x = UINT64_MAX;
    if (!Raw64_valid(x) || Raw64_encode(x) != x || Raw64_value(x) != x) return 1;
    if (End64_empty(x) != 0 || End64_set_empty(x, 9) != x) return 2;
    if (End64_encode(x, 0) != x || End64_admits(x, 1)) return 3;""",
        "Many": f"""if (!ManyFields_admits({zeroes})) return 1;
    if (ManyFields_encode({zeroes}) != 37 || ManyFields_f0(37) != 37) return 2;
    if (ManyFields_set_f64(37, 9) != 37) return 3;""",
        "Deep": """uint64_t x = UINT64_C(0x9e3779b97f4a7c15);
    for (unsigned i = 0; i < 10000; ++i) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        if (!DeepPrefix_valid(x) || DeepPrefix_value(x) != x || DeepPrefix_encode(x) != x) return 1;
    }
    if (DeepPrefix_value(UINT64_MAX) != UINT64_MAX || DeepPrefix_encode(UINT64_MAX) != UINT64_MAX) return 2;""",
    }
    for cc in ("gcc", "clang"):
        for theory, body in bodies.items():
            c = WORK / (theory + ".c")
            c.write_text(f'#include "{theory}.h"\nint main(void) {{\n    {body}\n    return 0;\n}}\n')
            exe = WORK / (theory + "-" + cc)
            run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic", c, "-o", exe])
            run([exe])

    # Logical fields are allowed to be C library names or keywords. Neither
    # macro formals nor printed parameter binders may capture helper tokens.
    symbols = ROOT / "tests/layouts/Symbols.module"
    run([binary, "layout", "header", symbols, "Names", "-o", WORK / "Names.h"])
    probe = WORK / "names.c"
    probe.write_text('#include "Names.h"\n'
                     'inline uint64_t bridge(uint64_t input) {\n'
                     ' return CESTA_LAYOUT_Names_encode_BODY(input, 2, 3, 4, 5, 6, 7, 8);\n}\n'
                     'extern uint64_t bridge(uint64_t input);\n'
                     'int main(void) {\n'
                     ' uint64_t word = Names_encode(1, 2, 3, 4, 5, 6, 7, 8);\n'
                     ' return bridge(1) != word || Names_UINT64_C(word) != 1 ||\n'
                     ' Names_uint64_t(word) != 2 || Names_bool(word) != 4;\n}\n')
    for cc in ("gcc", "clang"):
        for optimization in ("-O0", "-O2"):
            exe = WORK / ("names-" + cc + optimization)
            run([cc, "-std=c11", optimization, "-Wall", "-Wextra", "-Werror", "-pedantic", probe, "-o", exe])
            run([exe])

    def module(name, layout, field="value", location="immutable"):
        return (f'Module {name} {{ Theory Same() {{ Empty Equations {{ '
                f'({layout}) == (Layout "cesta-layout/1" (Word 8) '
                f'(Fields (Field {field} "value")) (Case (Place {field} (Bits 0 8))) '
                f'(Location "{location}")); }} }} }}\n')

    # Separate modules may use the same theory name; both headers must remain visible.
    for name in ("First", "Second"):
        path = WORK / (name + ".module")
        path.write_text(module(name, name))
        run([binary, "layout", "header", path, "Same", "-o", WORK / (name + ".h")])
    probe = WORK / "headers.c"
    probe.write_text('#include "First.h"\n#include "Second.h"\n'
                     'int main(void) { return First_value(9) != Second_value(9); }\n')
    for cc in ("gcc", "clang"):
        exe = WORK / ("headers-" + cc)
        run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", probe, "-o", exe])
        run([exe])

    # Public C symbols must be distinct, including generated atomic helpers.
    first = module("Collision", "A", "b_c")
    second = module("Unused", "A_b", "c").split("Empty Equations {", 1)[1].rsplit("}", 3)[0]
    collision = first.replace("; } } }", "; " + second + " } } }")
    cases = [(collision, "A_b_c"), (module("Atomic", "A", "atomic_load", "atomic"), "A_atomic_load")]
    for i, (source, name) in enumerate(cases):
        path = WORK / f"collision-{i}.module"
        path.write_text(source)
        result = subprocess.run([str(binary), "layout", "check", str(path), "Same"],
                                capture_output=True, text=True)
        assert result.returncode and "same C name " + name in result.stderr, result.stderr
    print("layout edges: full 64-bit words, empty terminal slots, 65 fields, 64-bit prefixes, "
          "independent header guards and refused symbol collisions passed (GCC and Clang)" +
          ("; generator passed ASan/UBSan" if args.sanitize else ""))


if __name__ == "__main__":
    main()
