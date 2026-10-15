"""Extension controls independent of which constructs appear in the corpora."""

import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments):
    result = subprocess.run(list(map(str, arguments)), cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{arguments}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def main():
    checks = 0
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        work = Path(temporary)
        native = work / "native" / "unit.c"
        native.parent.mkdir()
        native.write_bytes((ROOT / "tests/semantics/gnu_extensions.c").read_bytes())
        ast = run([BINARY, "read", native])
        for head in ("GNUIdent", "GNUAttributeSpecifiers", "GNUAttributedLabelDeclaration",
                     "GNUCaseDeclaration", "GNUCaseRangeDeclaration", "GNUDefaultDeclaration",
                     "GNUAttributeStatement"):
            if not re.search(rb'\(c:' + head.encode() + rb'(?=[ )])', ast):
                raise RuntimeError("intended extension tree absent: " + head)
            checks += 1
        objects = {}
        for compiler in ("gcc", "clang"):
            run([BINARY, "types", "--dialect=" + compiler, native])
            probes = work / "probes.c"
            probes.write_bytes(run([BINARY, "probe", "--dialect=" + compiler, native]))
            run([compiler, "-std=gnu11", "-w", "-fsyntax-only", probes])
            obj = work / (compiler + ".o")
            run([compiler, "-std=gnu11", "-w", "-O2", "-g0", "-c", native, "-o", obj])
            objects[compiler] = obj.read_bytes()
            checks += 2
        for presentation in ("ast/1", "compact-c/1", "compact-c/2", "cmera-style/1"):
            document = work / "program.cesta"
            document.write_bytes(run([BINARY, "view", "--as", presentation, native]))
            printed = work / presentation.replace("/", "-") / "unit.c"
            printed.parent.mkdir()
            printed.write_bytes(run([BINARY, "write", document]))
            if run([BINARY, "read", printed]) != ast:
                raise RuntimeError("extension AST changed through " + presentation)
            checks += 1
            for compiler in objects:
                obj = work / (compiler + "-printed.o")
                run([compiler, "-std=gnu11", "-w", "-O2", "-g0", "-c", printed, "-o", obj])
                if obj.read_bytes() != objects[compiler]:
                    raise RuntimeError("extension object changed through " + presentation)
                checks += 1
        # GCC checks several builtin restrictions during code generation, so
        # -fsyntax-only cannot serve as the negative oracle here.
        source = work / "builtin.c"
        cases = [
            ("return __builtin_frame_address(x);", False, False),
            ("__builtin_prefetch(p,x); return p;", False, False),
            ("__builtin_prefetch(p,0,1.0); return p;", False, False),
            ("return __builtin_frame_address(0);", True, True),
            ("return __builtin_frame_address(-1);", True, False),
            ("__builtin_prefetch(p,0,4); return p;", True, False),
            ("__builtin_prefetch(p,0,1,2); return p;", True, False),
        ]
        for body, gcc, clang in cases:
            source.write_text("void *f(void *p, int x){" + body + "}\n")
            for compiler, expected in (("gcc", gcc), ("clang", clang)):
                native_result = subprocess.run([compiler, "-std=gnu11", "-w", "-O0", "-c", str(source),
                                                "-o", str(work / "builtin.o")], capture_output=True)
                result = subprocess.run([str(BINARY), "types", "--dialect=" + compiler, str(source)], capture_output=True)
                if (native_result.returncode == 0) != expected or (result.returncode == 0) != expected:
                    raise RuntimeError(f"{compiler} builtin verdict differs: {body}: {result.stderr.decode()}")
                checks += 1
        source.write_text("struct Bits { unsigned x:3; }; "
                          "struct Bits f(unsigned n){ struct Bits a={n}; struct Bits b={.x={n}}; "
                          "a.x=n; a.x+=n; struct Bits c={7}; struct Bits d={8}; "
                          "return (struct Bits){.x=n}; }\n")
        run(["gcc", "-std=gnu11", "-w", "-fsyntax-only", source])
        run(["clang", "-std=gnu11", "-w", "-fsyntax-only", source])
        census = run([BINARY, "census", source])
        rows = re.findall(rb"^ \(conversion [^\n]+\(store-bits 3\)\)$", census, re.M)
        if len(rows) != 6 or any(b" narrowing " not in row for row in rows):
            raise RuntimeError("bit-field initializer and assignment census lost a store: " + census.decode())
        from semantic_oracles import CENSUS
        if not all(CENSUS.fullmatch(row) for row in rows):
            raise RuntimeError("conversion oracle discarded bit-field store records")
        checks += 2
    print(f"GNU extension controls: {checks} intended trees, typed probes, exact presentations/objects and refusal checks")


if __name__ == "__main__":
    main()
