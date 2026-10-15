"""Module-driven macro contracts, native checks, effects and build dependencies."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-engine-tests"
MODULE = ROOT / "tests/modules/MacroControls.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n"
                             + result.stderr.decode(errors="replace")[-8000:])
    return result


def document(*forms, module=MODULE, theory="Arithmetic"):
    return '(cesta native-c/1 compact-c/2 (unit (use-macros ' + json.dumps(str(module)) + ' ' + theory + ') ' \
        + " ".join(forms) + "))\n"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    metadata = run([BINARY, "check-macros", MODULE, "Arithmetic"]).stdout
    assert b"(macros cesta-macro/1" in metadata and b"Evaluation" in metadata and b"LocalRef" in metadata
    for theory, message in [("WrongRepeat", b"occurrence count"), ("MissingBinder", b"no binder"),
                            ("MissingDeclaration", b"not declared"), ("WrongNamespace", b"different C namespace")]:
        result = run([BINARY, "check-macros", MODULE, theory], success=False)
        assert message in result.stderr, result.stderr
    text = MODULE.read_text()
    mutants = {
        "version": ('"cesta-macro/1"', '"cesta-macro/99"', b"contract"),
        "category": ('"addressable-modifiable"', '"fictional-category"', b"value category"),
        "profile": ('"gnu11"', '"fictional-profile"', b"profile"),
        "undefined-hole": ('(Argument x)', '(Argument absent)', b"unknown macro argument"),
        "wrong-hole-sort": ('(Template ("+" (Argument x) (Argument x)))',
                            '(Template ("+" (Argument x) (return 0)))', b"sort"),
        "hidden-free-name": ('(Template ("+" (Argument x) (Argument x)))',
                             '(Template ("+" (Argument x) hidden_caller))', b"LocalRef"),
        "bad-type-slot": ('(CType int)', '(CType int ("*" missing))', b"TypeSlot"),
        "head-collision": ('"twice-once"', '"+"', b"existing compact form"),
        "opaque-template": ('(Template ("+" (Argument x) (Argument x)))',
                            '(Template (native "x + x"))', b"opaque source"),
    }
    for name, (old, new, diagnostic) in mutants.items():
        path = WORK / (name + ".module"); path.write_text(text.replace(old, new, 1))
        result = run([BINARY, "check-macros", path, "Arithmetic"], success=False)
        assert diagnostic in result.stderr, (name, result.stderr)
    source = WORK / "program.cesta"
    source.write_text(document(
        '(native "int sequence; int tick(int x) { sequence = sequence * 10 + x; return x; }")',
        '(fn int main () '
        '(var int i 3) (var int tmp 90) (var int cesta_macro_0 200) (var int count 0) '
        '(var int a (twice-once (post++ i))) '
        '(var int b (ordered-sum (call tick 1) (call tick 2))) '
        '(var int lazy (choose-lazy 0 (post++ i) (post++ i))) '
        '(var int repeated (repeat-twice (post++ i))) '
        '(var int owned (local-plus-one tmp)) '
        '(var int updated (increment-place i)) '
        '(var int nested (twice-once (local-plus-one tmp))) '
        '(var int nested_effect (twice-once (twice-once (post++ i)))) '
        '(when 1 (+= count 1)) '
        '(return (! (&& (&& (&& (&& (&& (&& (&& (&& (&& (== a 6) (== b 3)) '
        '(== sequence 12)) (== lazy 4)) (== repeated 6)) (== owned 91)) (== updated 8)) '
        '(== i 9)) (== tmp 90)) (&& (&& (== nested 182) (== nested_effect 32)) '
        '(&& (== cesta_macro_0 200) (== count 1)))))))'))
    handwritten = WORK / "handwritten"; handwritten.mkdir(exist_ok=True)
    (handwritten / "program.c").write_text(
        "int sequence; int tick(int x) { sequence = sequence * 10 + x; return x; }\n"
        "int main(void) {\n"
        "int i=3, tmp=90, cesta_macro_0=200, count=0;\n"
        "int saved=i++; int a=saved+saved;\n"
        "int left=tick(1); int right=tick(2); int b=left+right;\n"
        "int condition=0; int lazy=condition ? i++ : i++;\n"
        "int repeated=(i++,i++); int owned=tmp+1; int updated=(i+=1);\n"
        "int nested=2*(tmp+1); int saved_nested=i++; int inner=saved_nested+saved_nested; "
        "int nested_effect=inner+inner;\n"
        "if (1) count+=1;\n"
        "return !(a==6 && b==3 && sequence==12 && lazy==4 && repeated==6 && owned==91 && "
        "updated==8 && i==9 && tmp==90 && nested==182 && nested_effect==32 && cesta_macro_0==200 && count==1);\n}\n")
    records = []
    for compiler in ("gcc", "clang"):
        project = WORK / compiler; project.mkdir(exist_ok=True)
        flags = ["-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror"]
        actual = project / "program.o"; reference = handwritten / (compiler + ".o")
        run([BINARY, "cc", "--cc", compiler, *flags, "-MMD", "-MP", "-c", source, "-o", actual])
        run([compiler, *flags, "-c", "program.c", "-o", reference], cwd=handwritten)
        assert actual.read_bytes() == reference.read_bytes(), compiler + ": handwritten object differs"
        executable = project / "program"
        run([compiler, actual, "-o", executable]); run([executable])
        assert str(MODULE) in actual.with_suffix(".d").read_text()
        dependencies = run([BINARY, "cc", "--cc", compiler, "-MM", source]).stdout
        assert str(MODULE).encode() in dependencies
        # This portable statement profile uses no statement expressions.
        portable = project / "portable.cesta"
        portable.write_text(document('(fn int main (void) (var int n 0) (when 1 (+= n 1)) (return (!= n 1)))'))
        run([BINARY, "cc", "--cc", compiler, "-std=c11", "-pedantic-errors", "-Wall", "-Wextra",
             "-Werror", portable, "-o", project / "portable"])
        run([project / "portable"])
        failures = [
            ("wrong-type", '(fn int main () (return (twice-once 1.0)))', b"Cesta macro argument type"),
            ("not-modifiable", '(fn int main () (return (increment-place 2)))', (b"lvalue", b"rvalue")),
            ("opaque-argument", '(fn int main () (return (twice-once (native "2"))))', b"opaque native"),
            ("wrong-arity", '(fn int main () (return (twice-once 1 2)))', b"requires 1"),
        ]
        for name, body, message in failures:
            path = project / (name + ".cesta"); path.write_text(document(body))
            result = run([BINARY, "cc", "--cc", compiler, *flags, "-c", path, "-o", project / (name + ".o")],
                         success=False)
            messages = message if isinstance(message, tuple) else (message,)
            assert any(word in result.stderr for word in messages), (name, result.stderr)
        # A module edit is an ordinary make dependency. The engine itself is
        # unchanged when a user introduces this new macro.
        module = project / "user.module"
        module.write_text('Module User { Theory Macros() { Empty Terms { '
            'User . Expr ::= "(" "user-plus" Expr ")"; } Equations { '
            '(User x) == (Macro "cesta-macro/1" (Profile "gnu11") '
            '(Arguments (Arg x (CType int) "value" (Evaluation "once"))) '
            '(Result (CType int) "value") (Locals) (Template ("+" (Argument x) 5))); } } }\n')
        user = project / "user.cesta"
        user.write_text(document('(fn int main () (return (!= (user-plus 2) 7)))', module=module, theory="Macros"))
        (project / "Makefile").write_text('.DEFAULT_GOAL := user\nCFLAGS := -std=gnu11 -O2 -g0 -Wall -Wextra -Werror\n'
            'CPPFLAGS := -MMD -MP\n' + f'include {ROOT}/cesta.mk\n'
            'user: user.o\n\t$(CC) user.o -o user\n-include user.d\n')
        args = ["make", "-s", "-j2", "CC=" + compiler, "CESTA=" + str(BINARY)]
        run(args, cwd=project); run([project / "user"])
        timestamp = (project / "user.o").stat().st_mtime_ns
        run(args, cwd=project); assert (project / "user.o").stat().st_mtime_ns == timestamp
        module.write_text(module.read_text().replace("(Argument x) 5", "(Argument x) 6"))
        run(args, cwd=project); assert (project / "user.o").stat().st_mtime_ns != timestamp
        failed = subprocess.run([str(project / "user")], cwd=project)
        assert failed.returncode == 1
        records.append({"compiler": compiler, "object_sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                        "portable_statement": True, "user_module_dependency": True})
        print(compiler + ": macro modules, native contracts, effects, objects and make passed", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records, "definition_mutants": len(mutants),
                                                 "negative_theories": 4,
                                                 "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
                                                 "module_sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest()},
                                                indent=2) + "\n")


if __name__ == "__main__":
    main()
