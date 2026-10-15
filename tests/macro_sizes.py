"""Checked allocation sizes: boundaries, effects, folding where overflow is impossible."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-sizes-tests"
MODULE = ROOT / "macros/Sizes.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


def document(*forms, module=MODULE):
    return '(cesta native-c/1 compact-c/2 (unit (include <stdint.h>) (include <stddef.h>) ' \
        '(include <stdlib.h>) (use-macros ' + json.dumps(str(module)) + ' Checked) ' + \
        '(native "typedef struct { uint64_t left, right; } Pair;") ' + " ".join(forms) + '))\n'


RUNTIME = r'''
static int order, length_calls, place_calls;
static size_t cells[2];
uint64_t length(void) { order=order*10+1; ++length_calls; return 3; }
size_t *place(void) { order=order*10+2; ++place_calls; return &cells[1]; }
int main(void) {
    size_t out=99; uint32_t small=99;
    if (!bytes32(UINT32_MAX,&out) || out!=(size_t)UINT32_MAX*sizeof(Pair)) return 1;
    out=99; if (!bytes64(SIZE_MAX/sizeof(Pair),&out) || out!=SIZE_MAX/sizeof(Pair)*sizeof(Pair)) return 2;
    out=99; if (bytes64(SIZE_MAX/sizeof(Pair)+1,&out) || out!=99) return 3;
    out=99; if (bytes_signed(-1,&out) || out!=99) return 4;
    if (!bytes_signed(5,&out) || out!=5*sizeof(Pair)) return 5;
    small=99; if (!bytes_narrow(UINT32_MAX/sizeof(Pair),&small) || small!=UINT32_MAX/sizeof(Pair)*sizeof(Pair)) return 6;
    small=99; if (bytes_narrow(UINT32_MAX/sizeof(Pair)+1,&small) || small!=99) return 7;
    small=99; if (!grow(0,&small) || small!=8) return 8;
    small=99; if (!grow(5,&small) || small!=10) return 9;
    small=99; if (!grow(UINT32_MAX/2,&small) || small!=UINT32_MAX-1) return 10;
    small=99; if (grow(UINT32_MAX/2+1,&small) || small!=99) return 11;
    if (!ordered() || order!=12 || length_calls!=1 || place_calls!=1 || cells[1]!=3*sizeof(Pair)) return 12;
    return 0;
}
'''

FUNCTIONS = (
    '(fn int bytes32 ((uint32_t length) (size_t (* out))) '
    '(return (array-bytes Pair uint32_t size_t length (* out))))',
    '(fn int bytes64 ((uint64_t length) (size_t (* out))) '
    '(return (array-bytes Pair uint64_t size_t length (* out))))',
    '(fn int bytes_signed (((long long) length) (size_t (* out))) '
    '(return (array-bytes Pair (long long) size_t length (* out))))',
    '(fn int bytes_narrow ((uint32_t length) (uint32_t (* out))) '
    '(return (array-bytes Pair uint32_t uint32_t length (* out))))',
    '(fn int grow ((uint32_t capacity) (uint32_t (* out))) '
    '(return (doubled-capacity uint32_t 8u capacity (* out))))',
    '(native "uint64_t length(void); size_t *place(void);")',
    '(fn int ordered (void) (return (array-bytes Pair uint64_t size_t (call length) (* (call place)))))',
)

HANDWRITTEN = (
    '#include <stdint.h>\n#include <stddef.h>\n#include <stdlib.h>\n'
    'typedef struct { uint64_t left, right; } Pair;\n'
    '#define ARRAY_BYTES(E,C,S,x,y) __extension__ ({ C saved=(x); S *target=&(y); '
    '__extension__ ({ S bytes; int accepted=0; '
    'if (!__builtin_mul_overflow(saved,sizeof(E),&bytes)) {*target=bytes;accepted=1;} accepted; }); })\n'
    '#define DOUBLED(C,i,c,y) __extension__ ({ C first=(i); C saved=(c); C *target=&(y); '
    '__extension__ ({ _Static_assert((C)-1>0,"doubled-capacity count must be unsigned"); '
    'C next=first; int accepted=1; '
    'if (saved) if (__builtin_add_overflow(saved,saved,&next)) accepted=0; '
    'if (accepted) *target=next; accepted; }); })\n'
    'int bytes32(uint32_t length,size_t *out) {return ARRAY_BYTES(Pair,uint32_t,size_t,length,*out);}\n'
    'int bytes64(uint64_t length,size_t *out) {return ARRAY_BYTES(Pair,uint64_t,size_t,length,*out);}\n'
    'int bytes_signed(long long length,size_t *out) {return ARRAY_BYTES(Pair,long long,size_t,length,*out);}\n'
    'int bytes_narrow(uint32_t length,uint32_t *out) {return ARRAY_BYTES(Pair,uint32_t,uint32_t,length,*out);}\n'
    'int grow(uint32_t capacity,uint32_t *out) {return DOUBLED(uint32_t,8u,capacity,*out);}\n'
    'uint64_t length(void); size_t *place(void);\n'
    'int ordered(void) {return ARRAY_BYTES(Pair,uint64_t,size_t,length(),*place());}\n')

# Where the count's range cannot overflow the size type, the checked form must
# cost nothing: its object equals an unchecked multiplication.
FOLDED = ('(fn size_t pair_bytes ((uint32_t length)) (block (var size_t bytes) '
          '(if (! (array-bytes Pair uint32_t size_t length bytes)) (call abort)) (return bytes)))')
UNCHECKED = ('#include <stdint.h>\n#include <stddef.h>\n#include <stdlib.h>\n'
             'typedef struct { uint64_t left, right; } Pair;\n'
             'size_t pair_bytes(uint32_t length) {return (size_t)length*sizeof(Pair);}\n')
KEPT = ('(fn size_t pair_bytes ((uint64_t length)) (block (var size_t bytes) '
        '(if (! (array-bytes Pair uint64_t size_t length bytes)) (call abort)) (return bytes)))')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "program.cesta"
    source.write_text(document(*FUNCTIONS, '(native ' + json.dumps(RUNTIME) + ')'))
    handwritten = WORK / "handwritten"; handwritten.mkdir(exist_ok=True)
    (handwritten / "program.c").write_text(HANDWRITTEN + RUNTIME)
    fold_dir = WORK / "fold"; fold_dir.mkdir(exist_ok=True)
    (fold_dir / "pair.cesta").write_text(document(FOLDED))
    (fold_dir / "unchecked").mkdir(exist_ok=True)
    (fold_dir / "unchecked" / "pair.c").write_text(UNCHECKED)
    (fold_dir / "kept.cesta").write_text(document(KEPT))
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
        folded = []
        for optimization in ("-O1", "-O2"):
            flags = ["-std=gnu11", optimization, "-g0", "-Wall", "-Wextra", "-Werror"]
            checked = fold_dir / (compiler + optimization + ".o")
            plain = fold_dir / "unchecked" / (compiler + optimization + ".o")
            run([BINARY, "cc", "--cc", compiler, *flags, "-c", "pair.cesta", "-o", checked], fold_dir)
            run([compiler, *flags, "-c", "pair.c", "-o", plain], fold_dir / "unchecked")
            assert checked.read_bytes() == plain.read_bytes(), compiler + optimization + ": check was not folded"
            kept = fold_dir / (compiler + optimization + "-kept.o")
            run([BINARY, "cc", "--cc", compiler, *flags, "-c", "kept.cesta", "-o", kept], fold_dir)
            symbols = run(["nm", kept], fold_dir).stdout
            assert b"abort" in symbols, compiler + optimization + ": a reachable overflow check disappeared"
            folded.append(optimization)
        exported = work / "exported.c"
        exported.write_bytes(run([BINARY, "expand", "--cc", compiler, source], work).stdout)
        run([compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", exported,
             "-o", work / "exported"], work); run([work / "exported"], work)
        negatives = (
            ("wrong-length", '(fn int main (void) (var size_t out 7) '
             '(return (array-bytes Pair uint32_t size_t 3 out)))', b"Cesta macro argument type"),
            ("wrong-out", '(fn int main (void) (var int out 7) '
             '(return (array-bytes Pair uint32_t size_t 3u out)))', b"Cesta macro argument type"),
            ("const-out", '(fn int main (void) (var (const size_t) out 7) '
             '(return (array-bytes Pair uint32_t size_t 3u out)))', b"Cesta macro argument type"),
            ("incomplete-element", '(native "struct opaque;") (fn int main (void) (var size_t out 7) '
             '(return (array-bytes (struct opaque) uint32_t size_t 3u out)))', None),
            ("signed-capacity", '(fn int main (void) (var int out 7) '
             '(return (doubled-capacity int 8 4 out)))', b"doubled-capacity count must be unsigned"),
            ("builtin-capture", '(define (__builtin_mul_overflow x y p) "0") '
             '(fn int main (void) (var size_t out 0) (return (array-bytes Pair uint32_t size_t 3u out)))',
             b"Cesta_macro_name_is_a_preprocessor_macro"),
        )
        for name, body, diagnostic in negatives:
            path = work / (name + ".cesta"); path.write_text(document(body))
            result = run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-c", path,
                          "-o", work / (name + ".o")], work, success=False)
            if diagnostic: assert diagnostic in result.stderr, result.stderr
        records.append({"compiler": compiler, "objects": objects, "folded_where_impossible": folded,
                        "check_kept_where_reachable": True, "failure_preserves_destination": True,
                        "ordered_once": True, "exported_native": True, "negative_controls": len(negatives)})
        print(compiler + ": size boundaries, effects, folding, export and identical objects passed", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        "module_sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
