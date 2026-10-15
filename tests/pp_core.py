"""Preprocessor core cases: the C standard's own examples of 6.10, its
#if arithmetic, hidesets, GNU and C23 forms, pragmas, line numbers, file
names and include search.

Each case is preprocessed by GCC and by Clang, and Cesta's preprocessor is
run configured as each of them; the token sequences must be identical.
Where the standard states a result, both compilers must also give it. The
error cases must be refused by both compilers and by Cesta.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests/pp"
# A case named *-c11.c runs with -std=c11; one named *-gcc.c is a GCC extension.
FLAGS = ["-std=gnu11", "-Iinclude", "-Iinclude/b"]

# The results 6.10.3.3-6.10.3.5 and 6.10.9 state, as source text.
STANDARD = {
    "standard-hash-hash.c": 'char p[] = "x ## y";',
    "standard-rescan-order.c": "2*9*g",
    "standard-example-3.c": """f(2 * (y+1)) + f(2 * (f(2 * (z[0])))) % f(2 * (0)) + t(1);
        f(2 * (2+(3,4)-0,1)) | f(2 * (~ 5)) & f(2 * (0,1))^m(0,1);
        int i[] = { 1, 23, 4, 5, };
        char c[2][6] = { "hello", "" };""",
    "standard-example-4.c": r'''printf("x" "1" "= %d, x" "2" "= %s", x1, x2);
        fputs("strncmp(\"abc\\0d\", \"abc\", '\\4') == 0" ": @\n", s);
        int included_vers2;
        "hello";
        "hello" ", world"''',
    "standard-example-5.c": "int j[] = { 123, 45, 67, 89, 10, 11, 12, };",
    "standard-example-7.c": r'''fprintf(stderr, "Flag" );
        fprintf(stderr, "X = %d\n", x );
        puts( "The first, second, and third items." );
        ((x>y)?puts("x>y"): printf("x is %d but y is %d", x, y));''',
    "standard-pragma-operator.c": r'#pragma listing on "..\listing.dir"',
}


def run(arguments, **options):
    return subprocess.run([str(x) for x in arguments], capture_output=True, cwd=CASES, **options)


def tokens(cesta, text):
    """The preprocessing tokens of a text, by Cesta's phase 1-3 scanner;
    GNU line markers (# number "file" flags) are not tokens of the program."""
    lines = [line for line in text.splitlines() if not (line.startswith("# ") and line[2:3].isdigit())]
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        probe = Path(temporary) / "tokens.i"
        probe.write_text("\n".join(lines)+"\n")
        result = run([cesta, "source", "--tokens", probe])
        if result.returncode: raise SystemExit("preprocessor core: token scan failed: "+result.stderr.decode(errors="replace"))
        return result.stdout.decode(errors="replace").splitlines()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta", type=Path, default=ROOT / "build/cesta")
    options = parser.parse_args()
    cesta = options.cesta.resolve()
    failures, compared, refused = [], 0, 0
    for path in sorted(CASES.glob("*.c")):
        expected = tokens(cesta, STANDARD[path.name]) if path.name in STANDARD else None
        flags = [("-std=c11" if path.stem.endswith("-c11") and x == "-std=gnu11" else x) for x in FLAGS]
        for compiler in ("gcc", "clang"):
            reference = run([compiler, *flags, "-E", "-P", path.name])
            ours = run([cesta, "pp", "--cc", compiler, *flags, path.name])
            if path.stem.endswith("-gcc") and compiler == "clang":
                # a GCC extension: Clang and Cesta configured as Clang refuse it
                if not reference.returncode or not ours.returncode:
                    failures.append(f"{path.name} clang: exit {reference.returncode} vs {ours.returncode}")
                else:
                    refused += 1
                continue
            if reference.returncode or ours.returncode:
                failures.append(f"{path.name} {compiler}: exit {reference.returncode} vs {ours.returncode}: "
                                f"{ours.stderr.decode(errors='replace').strip()[:200]}")
                continue
            ref, got = tokens(cesta, reference.stdout.decode(errors="replace")), tokens(cesta, ours.stdout.decode(errors="replace"))
            if ref != got:
                at = next((i for i,(a,b) in enumerate(zip(ref,got)) if a != b), min(len(ref),len(got)))
                failures.append(f"{path.name} {compiler}: token {at} differs: {ref[at:at+3]} vs {got[at:at+3]}")
            if expected is not None and ref != expected:
                failures.append(f"{path.name} {compiler}: the compiler's result differs from the standard's")
            compared += 1
    for path in sorted((CASES / "errors").glob("*.c")):
        for compiler in ("gcc", "clang"):
            reference = run([compiler, *FLAGS, "-E", "-P", "errors/"+path.name])
            ours = run([cesta, "pp", "--cc", compiler, *FLAGS, "errors/"+path.name])
            if not reference.returncode or not ours.returncode:
                failures.append(f"errors/{path.name} {compiler}: exit {reference.returncode} vs {ours.returncode}")
            else:
                refused += 1
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        wide = Path(temporary) / "wide.c"
        parameters = ",".join("p"+str(i) for i in range(512))
        arguments = ",".join(str(i) for i in range(512))
        variadic_parameters = ",".join("p"+str(i) for i in range(256))
        variadic_arguments = ",".join(str(i) for i in range(257))
        wide.write_text("#define WIDE("+parameters+") (p0+p511)\n"
                        "#define VAR("+variadic_parameters+",...) (p0+p255+__VA_ARGS__)\n"
                        "_Static_assert(WIDE("+arguments+")==511,\"wide\");\n"
                        "_Static_assert(VAR("+variadic_arguments+")==511,\"variadic boundary\");\n")
        for compiler in ("gcc", "clang"):
            reference = run([compiler,"-std=c11","-E","-P",wide])
            ours = run([cesta,"pp","--cc",compiler,"-std=c11","-P",wide])
            if reference.returncode or ours.returncode or tokens(cesta,reference.stdout.decode()) != tokens(cesta,ours.stdout.decode()):
                failures.append(compiler+": wide and variadic macro parameters differ: "+ours.stderr.decode()[:200])
            else:
                compared += 1
    if failures:
        for failure in failures: print("preprocessor core:", failure, file=sys.stderr)
        raise SystemExit(f"preprocessor core: {len(failures)} failures")
    print(f"preprocessor core: {compared} case runs token-identical to GCC and Clang "
          f"({len(STANDARD)} with the standard's stated results); {refused} error runs refused by all three")


if __name__ == "__main__":
    main()
