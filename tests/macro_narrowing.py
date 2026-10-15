"""Checked conversion library: boundaries, effects, failure and native export."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-narrowing-tests"
MODULE = ROOT / "macros/Narrowing.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


def document(*forms, module=MODULE):
    return '(cesta native-c/1 compact-c/2 (unit (include <stdint.h>) (use-macros ' + \
        json.dumps(str(module)) + ' Checked) ' + " ".join(forms) + '))\n'


RUNTIME = r'''
static int order, value_calls, place_calls;
static uint64_t cells[2];
uint64_t input(void) { order=order*10+1; ++value_calls; return 17; }
uint64_t *output(void) { order=order*10+2; ++place_calls; return &cells[1]; }
int main(void) {
    uint32_t u=99; signed char s=11; unsigned char c=22;
    if (!narrow64(UINT32_MAX,&u) || u!=UINT32_MAX) return 1;
    u=99; if (narrow64((uint64_t)UINT32_MAX+1,&u) || u!=99) return 2;
    for (long long n=-300;n<=300;++n) {
        s=11; int signed_ok=narrow_signed(n,&s);
        if (signed_ok!=(n>=-128 && n<=127) || (signed_ok ? s!=n : s!=11)) return 3;
        c=22; int unsigned_ok=narrow_unsigned(n,&c);
        if (unsigned_ok!=(n>=0 && n<=255) || (unsigned_ok ? c!=n : c!=22)) return 4;
    }
    s=11; if (narrow_wide(UINT64_MAX,&s) || s!=11) return 5;
    if (!ordered() || order!=12 || value_calls!=1 || place_calls!=1 || cells[1]!=17) return 6;
    return 0;
}
'''


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "program.cesta"
    source.write_text(document(
        '(fn int narrow64 ((uint64_t value) (uint32_t (* out))) '
        '(return (try-narrow uint64_t uint32_t value (* out))))',
        '(fn int narrow_signed (((long long) value) ((signed char) (* out))) '
        '(return (try-narrow (long long) (signed char) value (* out))))',
        '(fn int narrow_unsigned (((long long) value) ((unsigned char) (* out))) '
        '(return (try-narrow (long long) (unsigned char) value (* out))))',
        '(fn int narrow_wide ((uint64_t value) ((signed char) (* out))) '
        '(return (try-narrow uint64_t (signed char) value (* out))))',
        '(native "uint64_t input(void); uint64_t *output(void);")',
        '(fn int ordered (void) (return (try-narrow uint64_t uint64_t (call input) (* (call output)))))',
        '(native ' + json.dumps(RUNTIME) + ')'))
    handwritten = WORK / "handwritten"; handwritten.mkdir(exist_ok=True)
    (handwritten / "program.c").write_text('#include <stdint.h>\n'
        '#define TRY_NARROW(S,T,x,y) __extension__ ({ S saved=(x); T *place=&(y); '
        '__extension__ ({ T converted; int accepted=0; '
        'if (!__builtin_add_overflow(saved,0,&converted)) {*place=converted;accepted=1;} accepted; }); })\n'
        'int narrow64(uint64_t value,uint32_t *out) {return TRY_NARROW(uint64_t,uint32_t,value,*out);}\n'
        'int narrow_signed(long long value,signed char *out) {return TRY_NARROW(long long,signed char,value,*out);}\n'
        'int narrow_unsigned(long long value,unsigned char *out) {return TRY_NARROW(long long,unsigned char,value,*out);}\n'
        'int narrow_wide(uint64_t value,signed char *out) {return TRY_NARROW(uint64_t,signed char,value,*out);}\n'
        'uint64_t input(void); uint64_t *output(void);\n'
        'int ordered(void) {return TRY_NARROW(uint64_t,uint64_t,input(),*output());}\n' + RUNTIME)
    records = []
    for compiler in ("gcc", "clang"):
        work = WORK / compiler; work.mkdir(exist_ok=True)
        objects = []
        for optimization in ("-O0", "-O2"):
            flags = ["-std=gnu11", optimization, "-g0", "-Wall", "-Wextra", "-Werror"]
            actual = work / (optimization + ".o"); reference = handwritten / (compiler + optimization + ".o")
            run([BINARY, "cc", "--cc", compiler, *flags, "-c", source, "-o", actual], work)
            run([compiler, *flags, "-c", "program.c", "-o", reference], handwritten)
            assert actual.read_bytes() == reference.read_bytes(), compiler + optimization + ": object differs"
            executable = work / (optimization + "-program")
            run([compiler, actual, "-o", executable], work); run([executable], work)
            objects.append({"optimization": optimization, "sha256": hashlib.sha256(actual.read_bytes()).hexdigest()})
        exported = work / "exported.c"
        exported.write_bytes(run([BINARY, "expand", "--cc", compiler, source], work).stdout)
        run([compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", exported,
             "-o", work / "exported"], work); run([work / "exported"], work)
        negatives = (
            ("wrong-input", '(fn int main (void) (var int out 7) '
             '(return (try-narrow (long long) int 3 out)))', b"Cesta macro argument type"),
            ("wrong-place", '(fn int main (void) (return (try-narrow int int 3 0)))', None),
            ("float-target", '(fn int main (void) (var double out 7.0) '
             '(return (try-narrow int double 3 out)))', None),
            ("float-input", '(fn int main (void) (var int out 7) '
             '(return (try-narrow double int 3.0 out)))', None),
            ("const-place", '(fn int main (void) (var (const int) out 7) '
             '(return (try-narrow int int 3 out)))', b"Cesta macro argument type"),
            ("bit-field", '(native "struct bits {unsigned value:3;};") '
             '(fn int main (void) (var (struct bits) record) '
             '(return (try-narrow int (unsigned int) 3 (. record value))))', None),
            ("builtin-capture", '(define (__builtin_add_overflow x y p) "0") '
             '(fn int main (void) (var int out 0) (return (try-narrow int int 3 out)))',
             b"Cesta_macro_name_is_a_preprocessor_macro"),
        )
        for name, body, diagnostic in negatives:
            path = work / (name + ".cesta"); path.write_text(document(body))
            result = run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-c", path,
                          "-o", work / (name + ".o")], work, success=False)
            if diagnostic: assert diagnostic in result.stderr, result.stderr
        records.append({"compiler": compiler, "objects": objects, "signed_boundaries": 601,
                        "unsigned_boundaries": 601, "failure_preserves_destination": True,
                        "ordered_once": True, "exported_native": True, "negative_controls": len(negatives)})
        print(compiler + ": narrowing boundaries, effects, preserved failures, export and identical objects passed", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        "module_sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
