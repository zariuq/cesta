"""Native prescan keeps preprocessing effects out of duplicated type checks."""

import hashlib
import json
from pathlib import Path
import subprocess
from oracles.pro_compact_cesta import parse

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-prescan-tests"
ARITHMETIC = ROOT / "tests/modules/MacroControls.module"


def run(arguments, cwd=WORK):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if result.returncode:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-7000:])
    return result


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    module = WORK / "body.module"
    module.write_text('Module Body { Theory Macros() { Empty Terms { '
        'Counter . Expr ::= "(" "counter-body" ")"; } Equations { '
        '(Counter) == (Macro "cesta-macro/1" (Profile "gnu11") (Arguments) '
        '(Result (CType int) "value") (Locals) '
        '(Template (Capture "ordinary" "__COUNTER__"))); } } }\n')
    cases = (
        ("once", "(twice-once __COUNTER__)", 0, ARITHMETIC, "Arithmetic"),
        ("repeat", "(repeat-twice (call tick (& n) __COUNTER__))", 0, ARITHMETIC, "Arithmetic"),
        ("deferred", "(choose-lazy 0 __COUNTER__ __COUNTER__)", 1, ARITHMETIC, "Arithmetic"),
        ("nested", "(twice-once (twice-once __COUNTER__))", 0, ARITHMETIC, "Arithmetic"),
        ("body", "(counter-body)", 0, module, "Macros"),
    )
    records = []
    for compiler in ("gcc", "clang"):
        work = WORK / compiler; work.mkdir(exist_ok=True)
        for name, expression, expected, definitions, theory in cases:
            source = work / (name + ".cesta")
            source.write_text('(cesta native-c/1 compact-c/2 (unit (use-macros ' +
                json.dumps(str(definitions)) + ' ' + theory + ') '
                '(native "int tick(int *n,int value) {++*n;return value;}") '
                '(fn int main (void) (var int n 0) '
                '(return (! (&& (== ' + expression + ' ' + str(expected) + ') (== n ' +
                ('2' if name == 'repeat' else '0') + ')))))))\n')
            parse(source.read_bytes())
            executable = work / name
            run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra",
                 "-Werror", source, "-o", executable], work)
            run([executable], work)
        records.append({"compiler": compiler, "cases": [case[0] for case in cases]})
        print(compiler + ": CPP effects prescan once in arguments, nested calls and result bodies", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
