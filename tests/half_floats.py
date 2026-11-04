"""_Float16 and __bf16: reading, typing against GCC and Clang, and targets without them."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/half-float-tests"

# Every operand pairing of the usual arithmetic conversions, unary operators,
# conditionals, literals, assignments, prototypes and variadic arguments.
SOURCE = r"""
_Float16 h; __bf16 b; float f; double d; long double l; int i; unsigned long u; _Bool z;
_Float16 hs[3]; __bf16 *bp; _Complex _Float16 ch; _Float16 const _Complex cc;
_Float16 take(_Float16 x, __bf16 y) { return x + (_Float16)y; }
int pass(int n, ...);
struct record { _Float16 half; __bf16 brain; char tail; };
_Static_assert(sizeof(_Float16) == 2 && _Alignof(_Float16) == 2, "binary16");
_Static_assert(sizeof(__bf16) == 2 && _Alignof(__bf16) == 2, "bfloat16");
_Static_assert(sizeof(struct record) == 6, "record");
void use(void) {
    (void)(h + h); (void)(h + i); (void)(h + u); (void)(h + z); (void)(h + f); (void)(h + d); (void)(h + l);
    (void)(b + b); (void)(b + i); (void)(b + f); (void)(b + d); (void)(h + b); (void)(b * h);
    (void)(i ? h : b); (void)(i ? b : f); (void)(-h); (void)(+b); (void)(!h); (void)(h < b);
    (void)(1.5f16 * h); (void)(0.0F16 + i); (void)(0x1.8p1f16 + b); (void)(h += 1); (void)(b *= f);
    (void)(hs[1] + *bp); (void)(ch + h); (void)(cc * f); (void)take(h, b); (void)take(1, 2.5); (void)pass(2, h, b);
    (void)(_Float16)d; (void)(__bf16)i; (void)(int)h; (void)sizeof(h + b);
}
"""


def run(arguments, cwd, success=True, text=None):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True, input=text)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\n{result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-4000:])
    return result


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "half.c"
    source.write_text(SOURCE)
    records = []
    for compiler, dialect in (("gcc", "gcc"), ("clang", "clang")):
        # i386 has the types; arithmetic on them needs SSE2 there.
        for machine, abi in (("-m64", "x86_64-sysv-lp64"), ("-m32 -msse2", "i386-sysv-ilp32")):
            flags = machine.split(); machine = flags[0]
            preprocessed = WORK / f"half-{compiler}{machine}.i"
            preprocessed.write_bytes(run([compiler, "-std=gnu11", *flags, "-E", "-P", source], WORK).stdout)
            probe = run([BINARY, "probe", "--std=gnu11", "--dialect=" + dialect, "--abi=" + abi,
                         preprocessed], WORK)
            checked = WORK / f"half-{compiler}{machine}.probe.c"
            checked.write_bytes(probe.stdout)
            run([compiler, "-std=gnu11", *flags, "-Werror", "-fsyntax-only", checked], WORK)
            summary = probe.stderr.decode()
            typed = int(summary.split("probes: ")[1].split(" typed")[0])
            assert typed >= 150, summary
            records.append({"compiler": compiler, "abi": abi, "typed_expressions": typed})
        # GCC's own vector headers (Clang's need __builtin_bit_cast, which is
        # not part of this grammar yet): gcc 15 declares its half-precision
        # intrinsics with _Float16, _Float16 _Complex, __bf16 and f16 constants.
        # Read, print and recompile a unit that includes them; the object must
        # not change.  (Typing the headers needs the ia32 builtin signatures.)
        if compiler != "gcc":
            continue
        unit = WORK / f"intrinsics-{compiler}.c"
        unit.write_text("#include <immintrin.h>\n"
                        "_Float16 scale(_Float16 x, __bf16 y) { return x * (_Float16)y + 1.5f16; }\n"
                        "__m128 add(__m128 a, __m128 b) { return _mm_add_ps(a, b); }\n")
        flags = ["-std=gnu11", "-O2"]
        tree = run([BINARY, "read", "--cc", compiler, *flags, unit], WORK).stdout
        (WORK / f"intrinsics-{compiler}.sexp").write_bytes(tree)
        printed_dir = WORK / f"printed-{compiler}"; printed_dir.mkdir(exist_ok=True)
        printed = printed_dir / unit.name
        printed.write_bytes(run([BINARY, "write", "--std=gnu11", WORK / f"intrinsics-{compiler}.sexp"], WORK).stdout)
        run([compiler, *flags, "-c", unit.name, "-o", f"intrinsics-{compiler}.o"], WORK)
        run([compiler, *flags, "-c", unit.name, "-o", f"../intrinsics-{compiler}.printed.o"], printed_dir)
        assert (WORK / f"intrinsics-{compiler}.o").read_bytes() == (WORK / f"intrinsics-{compiler}.printed.o").read_bytes(), compiler
        records.append({"compiler": compiler, "header": "immintrin.h", "tree_bytes": len(tree), "object_identical": True})
    # Combinations the compilers refuse are refused.
    for declaration in ("_Complex __bf16 x;", "long _Float16 x;", "unsigned _Float16 x;",
                        "_Float16 _Float16 x;", "_Float16 float x;"):
        unit = WORK / "refused.c"
        unit.write_text(declaration + "\n")
        refused = run([BINARY, "types", "--std=gnu11", unit], WORK, success=False)
        assert b"invalid type specifier combination" in refused.stderr or b"syntax" in refused.stderr, refused.stderr
        for compiler in ("gcc", "clang"):
            run([compiler, "-std=gnu11", "-fsyntax-only", unit], WORK, success=False)
    # A target without the formats refuses the types instead of guessing a layout.
    for spelling in ("_Float16", "__bf16"):
        unit = WORK / f"absent-{spelling}.c"
        unit.write_text(f"{spelling} x;\n")
        refused = run([BINARY, "types", "--std=gnu11", "--abi=x86_64-windows-llp64", unit], WORK, success=False)
        assert b"is not available on this target" in refused.stderr, refused.stderr
    (WORK / "summary.json").write_text(json.dumps({"records": records}, indent=2) + "\n")
    print("half floats: arithmetic, literals and layouts typed as GCC and Clang type them on x86-64 and i386; "
          "immintrin.h read, printed and recompiled to the same object; absent on Windows", flush=True)


if __name__ == "__main__":
    main()
