"""Identifier identity, stringification, native objects and changed-length origins."""

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(arguments):
    result = subprocess.run(list(map(str, arguments)), cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta", type=Path, default=ROOT / "build/cesta")
    binary = parser.parse_args().cesta.resolve()
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        work = Path(temporary)
        source = work / "unit.c"
        source.write_bytes((ROOT / "tests/pp/unicode-identifiers.c").read_bytes())
        for compiler in ("gcc", "clang"):
            original, expanded = work / (compiler + "-original"), work / (compiler + "-expanded")
            original.mkdir(); expanded.mkdir()
            (original / "unit.i").write_bytes(run([compiler, "-std=gnu11", "-E", "-P", source]))
            provenance = work / "origins.txt"
            text = run([binary, "pp", "--cc", compiler, "-std=gnu11", "-P",
                        "--provenance", provenance, source])
            (expanded / "unit.i").write_bytes(text)
            for directory in (original, expanded):
                run([compiler, "-std=gnu11", "-O2", "-g0", "-c", directory / "unit.i",
                     "-o", directory / "unit.o"])
            if (original / "unit.o").read_bytes() != (expanded / "unit.o").read_bytes():
                raise RuntimeError(compiler + ": Unicode preprocessing changed the object")
            tree = work / "unit.csexp"
            tree.write_bytes(run([binary, "read", expanded / "unit.i"]))
            printed = work / "printed.i"
            printed.write_bytes(run([binary, "write", tree]))
            if run([binary, "read", printed]) != tree.read_bytes():
                raise RuntimeError(compiler + ": Unicode identifier spellings did not round trip")
            # An emitted identifier can be longer than its original spelling.
            # The provenance extent must cover all its emitted bytes.
            needle = br"\U000003b4" if compiler == "gcc" else "δ".encode()
            offset = text.index(needle)
            rows = [re.match(rb"(\d+) (\d+) .* (\d+) (\d+)(?: |$)", line)
                    for line in provenance.read_bytes().splitlines()]
            extent = next((row for row in rows if row and int(row[1]) == offset), None)
            source_line = next(i for i, line in enumerate(source.read_bytes().splitlines(), 1)
                               if line.startswith(br"int \u03b4 ="))
            if not extent or int(extent[2]) != len(needle) or int(extent[3]) != source_line or int(extent[4]) != 5:
                raise RuntimeError(compiler + ": output normalization changed the origin extent")
        # C scopes already use the same identity key. Exercise all four name
        # spaces with mixed spellings, independently of preprocessing output.
        source.write_text(r'''typedef int \u03b1;
struct \u03b2 { α \u03b3; };
int f(α δ) { struct β v = { .γ = δ }; goto \u03b5;
ε: return v.γ; }
''')
        for compiler in ("gcc", "clang"):
            run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", source])
            run([binary, "types", "--std=c11", "--dialect=" + compiler, source])
        # Equal code points have equal names; Unicode normalization is not a
        # C name-equivalence rule. These two names deliberately remain distinct.
        source.write_text("#define é 1\n#define é 2\nint n = é + é;\n")
        for compiler in ("gcc", "clang"):
            output = run([binary, "pp", "--cc", compiler, "-std=c11", "-P", source])
            if b"int n = 1 + 2;" not in output:
                raise RuntimeError(compiler + ": distinct Unicode scalar sequences were merged")
    print("Unicode names: two exact native objects and AST round trips; changed-length origins; "
          "four C name spaces; distinct Unicode normalization forms")


if __name__ == "__main__":
    main()
