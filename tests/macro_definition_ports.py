"""Native definition bindings survive caller shadowing and CPP aliases."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-definition-port-tests"
MODULE = ROOT / "tests/modules/MacroPorts.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\n{result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-6000:])
    return result


def doc(*forms, module=MODULE):
    return '(cesta native-c/1 compact-c/2 (unit ' + " ".join(forms) + '))\n'


def use(module=MODULE):
    return "(use-macros " + json.dumps(str(module)) + " NativeFunctions)"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    metadata = run([BINARY, "check-macros", MODULE, "NativeFunctions"]).stdout
    assert b"cesta-macro/2" in metadata and b"DefinitionRef" in metadata
    missing = run([BINARY, "check-macros", MODULE, "MissingPort"], success=False)
    assert b"no native symbol port" in missing.stderr
    records = []
    for compiler in ("gcc", "clang"):
        project = WORK / compiler; project.mkdir(exist_ok=True)
        source = project / "program.cesta"
        source.write_text(doc("(include <stdlib.h>)", '(define (abs x) "900")', use(),
            '(fn int main (void) (var int abs 333) (var int n (- 7)) '
            '(var int got (native-abs (post++ n))) '
            '(return (! (&& (&& (== got 7) (== n (- 6))) (&& (== abs 333) (== (call abs 1) 900))))))'))
        reference_dir = project / "reference"; reference_dir.mkdir(exist_ok=True)
        (reference_dir / "program.c").write_text(
            '#include <stdlib.h>\n#define abs(x) 900\n#pragma push_macro("abs")\n#undef abs\n'
            'typedef int fn(int);\nstatic fn *const owned = &abs;\n#pragma pop_macro("abs")\n'
            'int main(void) {int abs=333, n=-7; int got=owned(n++); '
            'return !(got==7 && n==-6 && abs==333 && abs(1)==900);}\n')
        flags = ["-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror"]
        actual = project / "actual.o"; reference = reference_dir / "reference.o"
        run([BINARY, "cc", "--cc", compiler, *flags, "-c", source, "-o", actual], project)
        run([compiler, *flags, "-c", "program.c", "-o", reference], reference_dir)
        assert actual.read_bytes() == reference.read_bytes(), compiler + ": definition port object differs"
        executable = project / "program"
        run([compiler, actual, "-o", executable], project); run([executable], project)
        # The CPP alias is restored after the native binding is acquired.
        (project / "provider.h").write_text('#ifndef PROVIDER_H\n#define PROVIDER_H\n'
            'static inline int provider(int x) { return x+10; }\n#endif\n')
        custom = project / "custom.cesta"
        custom.write_text(doc('(include "provider.h")', '(define provider "caller_provider")', use(),
            '(fn int main (void) (var int provider 99) (var int n 2) '
            '(var int got (apply-native (post++ n))) '
            '(return (! (&& (== got 12) (&& (== n 3) (== provider 99))))))'))
        run([BINARY, "cc", "--cc", compiler, *flags, "-I", project, custom, "-o", project / "custom"], project)
        run([project / "custom"], project)
        (project / "object_provider.h").write_text("extern int state;\n")
        obj_source = project / "object.cesta"
        obj_source.write_text(doc('(include "object_provider.h")', '(var int state 4)',
            '(native "int observed_state(void) { return state; }")',
            "(use-macros " + json.dumps(str(MODULE)) + " NativeObjects)",
            '(fn int main (void) (var int state 77) (var int n 3) '
            '(var int got (add-native-state (post++ n))) '
            '(return (! (&& (&& (== got 7) (== n 4)) (&& (== state 77) (== (call observed_state) 7))))))'))
        run([BINARY, "cc", "--cc", compiler, *flags, "-I", project, obj_source, "-o", project / "object"], project)
        run([project / "object"], project)
        # Port type constraints are hard compiler assertions, not optional
        # pointer-conversion warnings.
        wrong = project / "wrong.module"
        wrong.write_text(MODULE.read_text().replace('(CType int ("function" TypeSlot int))',
                                                   '(CType long ("function" TypeSlot int))', 1))
        bad = project / "wrong.cesta"
        bad.write_text(doc(use(wrong), '(fn int main (void) (return (native-abs 1)))'))
        result = run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-c", bad, "-o", project / "wrong.o"],
                     project, success=False)
        assert b"Cesta native port type" in result.stderr
        # Unused theory ports do not demand unavailable headers.
        assert b"provider.h" not in run([BINARY, "expand", "--cc", compiler, source], project).stdout
        records.append({"compiler": compiler, "object_sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                        "caller_shadowing": True, "CPP_restoration": True, "hard_type_refusal": True})
        print(compiler + ": definition ports preserve bindings, effects, objects and CPP state", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
