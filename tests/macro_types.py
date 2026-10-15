"""Sorted type holes and compiler-owned type/category admission."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-type-tests"
MODULE = ROOT / "tests/modules/MacroTypes.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-7000:])
    return result


def document(body, module=MODULE):
    return '(cesta native-c/1 compact-c/2 (unit (include <stddef.h>) (use-macros ' + \
        json.dumps(str(module)) + ' GenericValues) ' + body + '))\n'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    for theory in ("WrongTypeHole", "MissingTypeHole"):
        result = run([BINARY, "check-macros", MODULE, theory], success=False)
        assert b"DeclarationSpecifiers syntax argument" in result.stderr
    records = []
    for compiler in ("gcc", "clang"):
        work = WORK / compiler; work.mkdir(exist_ok=True)
        generic = work / "generic.cesta"
        generic.write_text(document('(fn int main (void) (var int n 7) '
            '(var size_t size 3u) (var int x (typed-identity int (post++ n))) '
            '(var size_t y (typed-identity size_t (post++ size))) '
            '(var int out 0) (typed-store int out x) '
            '(return (! (&& (&& (== out 7) (== n 8)) (&& (== y 3u) (== size 4u))))))'))
        portable = work / "portable.cesta"
        portable.write_text(document('(fn int main (void) (var int n 4) (var int out 0) '
            '(typed-store int out (post++ n)) (return (! (&& (== out 4) (== n 5)))))'))
        flags = ["-Wall", "-Wextra", "-Werror"]
        for optimization in ("-O0", "-O2"):
            run([BINARY, "cc", "--cc", compiler, "-std=gnu11", optimization, *flags,
                 generic, "-o", work / "generic"], work)
            run([work / "generic"], work)
            run([BINARY, "cc", "--cc", compiler, "-std=c11", "-pedantic-errors", optimization,
                 *flags, portable, "-o", work / "portable"], work)
            run([work / "portable"], work)
        pointer_module = work / "pointer.module"
        pointer_module.write_text(MODULE.read_text().replace('(CType (TypeArgument t)) "value"',
            '(CType (TypeArgument t) ("*" TypeSlot)) "value"'))
        pointer = work / "pointer.cesta"
        pointer.write_text(document('(fn int main (void) (var int n 9) '
            '(var int (* p) (typed-identity int (& n))) (return (!= (* p) 9)))', pointer_module))
        run([BINARY, "cc", "--cc", compiler, "-std=gnu11", *flags,
             pointer, "-o", work / "pointer"], work)
        run([work / "pointer"], work)
        for name, body, diagnostic in (
            ("wrong-value", '(fn int main (void) (return (typed-identity int 1.0)))', b"Cesta macro argument type"),
            ("wrong-sort", '(fn int main (void) (return (typed-identity (post++ n) 1)))', b"DeclarationSpecifiers"),
            ("wrong-place", '(fn int main (void) (typed-store int 3 4) (return 0))', None),
            ("wrong-target", '(fn int main (void) (var long out 0) (typed-store int out 4) (return 0))',
             b"Cesta macro argument type"),
        ):
            source = work / (name + ".cesta"); source.write_text(document(body))
            result = run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-c", source,
                          "-o", work / (name + ".o")], work, success=False)
            if diagnostic: assert diagnostic in result.stderr, result.stderr
        records.append({"compiler": compiler, "generic_types": True, "pointer_type": True,
                        "strict_c11": True, "native_negatives": 4})
        print(compiler + ": sorted type parameters, typedefs, pointers and C11 statements passed", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records, "definition_negatives": 2,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
