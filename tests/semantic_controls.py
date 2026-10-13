"""Semantic controls: constraint violations both GCC and Clang reject, their valid
neighbours, one program under three data models, and conversion mutations.

Each case's verdict is first confirmed with both compilers, so a control
cannot drift from what C requires.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [("x86_64-sysv-lp64", "x86_64-linux-gnu"), ("i386-sysv-ilp32", "i386-linux-gnu"),
           ("x86_64-windows-llp64", "x86_64-pc-windows-msvc")]

CASES = [
 # (name, source, valid)
 ("assign-const", "void f(void){ const int x = 1; x = 2; }", False),
 ("assign-const-member", "struct S { const int a; }; void f(struct S *s){ s->a = 2; }", False),
 ("assign-const-struct", "struct S { const int a; }; void f(struct S *s, struct S t){ *s = t; }", False),
 ("assign-array", "void f(void){ int a[2], b[2]; a = b; }", False),
 ("increment-const", "void f(const int *p){ ++*p; }", False),
 ("address-bitfield", "struct S { int b : 3; }; int *f(struct S *s){ return &s->b; }", False),
 ("void-value", "void g(void); int f(void){ int x = g(); return x; }", False),
 ("missing-member", "struct S { int a; }; int f(struct S s){ return s.b; }", False),
 ("too-many-arguments", "int g(int); int f(void){ return g(1, 2); }", False),
 ("too-few-arguments", "int g(int, int); int f(void){ return g(1); }", False),
 ("call-non-function", "int f(int x){ return x(1); }", False),
 ("subscript-non-pointer", "int f(int x){ return x[1]; }", False),
 ("dereference-non-pointer", "int f(int x){ return *x; }", False),
 ("struct-arithmetic", "struct S { int a; }; int f(struct S s){ return s + 1; }", False),
 ("struct-to-int", "struct S { int a; }; int f(struct S s){ int x = s; return x; }", False),
 ("double-to-pointer", "void f(void){ int *p = 3.0; (void)p; }", False),
 ("undeclared", "int f(void){ return y; }", False),
 ("undefined-label", "void f(void){ goto out; }", False),
 ("case-not-constant", "int f(int x, int y){ switch (x) { case y: return 1; } return 0; }", False),
 ("increment-rvalue", "int g(void); void f(void){ g()++; }", False),
 ("sizeof-incomplete", "struct S; unsigned long f(void){ return sizeof(struct S); }", False),
 ("negative-array", "int a[-1];", False),
 ("static-assert-false", "_Static_assert(1 - 1, \"no\");", False),
 ("dot-on-pointer", "struct S { int a; }; int f(struct S *s){ return s.a; }", False),
 ("arrow-on-struct", "struct S { int a; }; int f(struct S s){ return s->a; }", False),
 ("typedef-conflict", "typedef int T; typedef long T;", False),
 ("function-conflict", "int g(int); long g(int);", False),
 ("object-conflict", "extern int x; extern long x;", False),
 ("tag-kind-conflict", "struct S { int a; }; union S *p;", False),
 ("tag-redefinition", "struct S { int a; }; struct S { int b; };", False),
 ("conditional-mismatch", "struct S { int a; }; int f(int c, struct S s){ return c ? s : 1; }", False),
 ("shift-pointer", "int f(int *p){ return p << 1; }", False),
 # Additional independent declaration and control constraints.
 ('void-param', 'int f(void x){return 1;}', False),
 ('duplicate-parameter', 'int f(int x, int x){return x;}', False),
 ('anonymous-member-conflict', 'struct S { struct { int x; }; int x; };', False),
 ('duplicate-function', 'int f(void){return 1;} int f(void){return 2;}', False),
 ('duplicate-local', 'int f(void){int x; int x;return 0;}', False),
 ('object-typedef-conflict', 'int x; typedef int x;', False),
 ('parameter-redeclaration', 'int f(int x){int x;return x;}', False),
 ('not-struct', 'struct S{int x;}; int f(struct S s){return !s;}', False),
 ('increment-struct', 'struct S{int x;}; void f(struct S s){++s;}', False),
 ('wrong-string-array', 'int a[]="abc";', False),
 ('array-scalar-initializer', 'int a[2]=1;', False),
 ('incomplete-parameter-definition', 'struct S; int f(struct S x){return 0;}', False),
 ('incomplete-old-parameter', 'struct S; int f(x) struct S x; {return 0;}', False),
 ('void-member', 'struct S { void x; };', False),
 ('function-member', 'struct S { int f(void); };', False),
 ('duplicate-member', 'struct S { int x; int x; };', False),
 ('function-returns-array', 'int f(void)[2];', False),
 ('function-returns-function', 'int f(void)(void);', False),
 ('void-array', 'void a[2];', False),
 ('function-array', 'int (a[2])(void);', False),
 ('incomplete-object', 'struct S; struct S x;', False),
 ('break-outside', 'int f(void){break;return 0;}', False),
 ('continue-outside', 'int f(void){continue;return 0;}', False),
 ('case-outside', 'int f(void){case 1:return 0;}', False),
 ('duplicate-case', 'int f(int x){switch(x){case 1: return 0; case 1:return 2;}return 0;}', False),
 ('duplicate-default', 'int f(int x){switch(x){default:return 0;default:return 1;}}', False),
 ('generic-duplicate', 'int f(int x){return _Generic(x,int:1,int:2);}', False),
 ('generic-default-twice', 'int f(int x){return _Generic(x,default:1,default:2);}', False),
 ('extern-static', 'extern int x; static int x;', False),
 ('flex-array-not-last', 'struct S { int x[]; int y; };', False),
 ('negative-bitwidth', 'struct S { int x:-1; };', False),
 ('wide-bitwidth', 'struct S { int x:64; };', False),
 ('named-zero-bitwidth', 'struct S { int x:0; };', False),
 ('float-bitfield', 'struct S { float x:3; };', False),
 ('oldstyle-bad-prototype', 'int f(int); int f(x) double x; { return 0; }', False),
 ('nested-loop-switch', 'int f(int x){ while(x){switch(x){case 1: break;default:continue;}break;}return 0;}', True),
 ('nested-switch-labels', 'int f(int x){switch(x){case 1:switch(x){case 1:break;default:break;}break;default:break;}return 0;}', True),
 ('case-converted-duplicate', 'int f(unsigned x){switch(x){case -1:return 0;case 4294967295u:return 1;}return 2;}', False),
 ('valid-flexible-array', 'struct S { int n; char data[]; };', True),
 ('valid-tentative-completion', 'struct S; struct S x; struct S { int n; };', True),
 ('valid-extern-incomplete', 'struct S; extern struct S x;', True),
 ('valid-static-extern', 'static int x; extern int x;', True),
 ('valid-oldstyle-promotion', 'int f(double); int f(x) float x; { return (int)x; }', True),
 ('generic-distinct-qualified', 'int f(int x){return _Generic(x,int:1,long:2,default:3);}', True),
 ('valid-vla', 'int f(int n){int a[n][n];a[0][0]=1;return a[0][0];}', True),
 ('local-shadow', 'int f(int x){{int x=1;(void)x;}return x;}', True),
 ('character-array-kinds', 'char a[]="a"; signed char b[]="b"; unsigned char c[]="c";', True),
 ('function-typedef', 'typedef int F(int); typedef F G; G f; int f(int x){return x;} int g(void){G f; return f(2);}', True),
 ('function-pointer-typedef', 'typedef int F(int); typedef F *P; int f(int x){return x;} P p=f;', True),
 # valid neighbours of each refusal
 ("assign-nonconst", "void f(void){ int x = 1; x = 2; (void)x; }", True),
 ("assign-member", "struct S { int a; }; void f(struct S *s){ s->a = 2; }", True),
 ("assign-struct", "struct S { int a; }; void f(struct S *s, struct S t){ *s = t; }", True),
 ("address-member", "struct S { int b; }; int *f(struct S *s){ return &s->b; }", True),
 ("call-void-statement", "void g(void); void f(void){ g(); }", True),
 ("prototype-call", "int g(int, int); int f(void){ return g(1, 2); }", True),
 ("variadic-call", "int g(int, ...); int f(void){ return g(1, 2.0, \"x\"); }", True),
 ("unprototyped-call", "int g(); int f(void){ return g(1, 2); }", True),
 ("subscript-commuted", "int f(int *p){ return 1[p]; }", True),
 ("function-pointer-call", "int f(int (*g)(int)){ return g(1) + (*g)(2); }", True),
 ("null-pointer", "void f(void){ int *p = 0; void *q = (void *)0; (void)p; (void)q; }", True),
 ("compatible-redeclarations", "int g(); int g(int); int g(int x){ return x; } extern int a[]; int a[3];", True),
 ("typedef-repeat", "typedef int T; typedef int T;", True),
 ("tag-completion", "struct S; struct S *p; struct S { int a; };", True),
 ("conditional-pointers", "const char *f(int c, char *a, const char *b){ return c ? a : b; }", True),
 ("enum-arithmetic", "enum E { A, B }; int f(enum E e){ return e + B; }", True),
 ("bitfield-promotion", "struct S { unsigned b : 3; }; int f(struct S s){ return s.b - 4 < 0; }", True),
 ("string-array", "char s[] = \"abc\"; _Static_assert(sizeof s == 4, \"\");", True),
 ("compound-literal", "int *f(void){ return (int[]){1, 2, 3}; }", True),
 ("designated", "struct P { int x, y; }; struct P p = { .y = 2, .x = 1 }; int a[] = { [4] = 1 }; _Static_assert(sizeof a == 20, \"\");", True),
 ("generic", "int f(long x){ return _Generic(x, int: 1, long: 2, default: 3); }", True),
 ("statement-expression", "int f(int x){ return ({ int y = x * 2; y + 1; }); }", True),
]


def compiles(compiler, path, *flags):
    return subprocess.run([compiler, "-std=gnu11", "-fsyntax-only", *flags, str(path)], capture_output=True).returncode == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=ROOT / "build/cesta")
    args = parser.parse_args()
    binary = str(args.binary)
    failures = []
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as work:
        work = Path(work)
        case = work / "case.c"
        for name, source, valid in CASES:
            case.write_text(source + "\n")
            if compiles("gcc", case) != valid or compiles("clang", case) != valid:
                failures.append(f"{name}: the compilers do not agree with the control")
                continue
            result = subprocess.run([binary, "types", "--std=gnu11", str(case)], capture_output=True)
            if (result.returncode == 0) != valid:
                failures.append(f"{name}: Cesta {'refused' if valid else 'accepted'} it: {result.stderr.decode()[:200]}")
            elif not valid and not result.stderr.decode().startswith(str(case) + ":"):
                failures.append(f"{name}: the refusal has no source location")
        # one program under each data model: probes pass on its own target only
        targets = ROOT / "tests/semantics/targets.c"
        for abi, _ in TARGETS:
            probe = work / (abi + ".c")
            probe.write_bytes(subprocess.run([binary, "probe", "--std=gnu11", "--dialect=clang", "--abi=" + abi, str(targets)],
                                             capture_output=True, check=True).stdout)
            for other, triple in TARGETS:
                if compiles("clang", probe, "--target=" + triple, "-w") != (other == abi):
                    failures.append(f"{abi} probes {'refused' if other == abi else 'accepted'} on {triple}")
        from semantic_oracles import layouts, probes
        for abi, triple in TARGETS:
            data = subprocess.run([binary, "types", "--dialect=clang", "--abi="+abi, str(targets)],
                                  capture_output=True, check=True).stdout
            compared = layouts(targets, {"corpus": "swi"}, data, "--target="+triple)
            if compared.get("disagree") or compared.get("unmatched") or not compared.get("agree"):
                failures.append(f"{abi}: record and bit-field layouts disagree with Clang: {dict(compared)}")
        # A cached/default ABI must not hide a type-affecting compiler option.
        case.write_text("struct S { char c; int n; };\n")
        for cc in ("gcc","clang"):
            accepted, _, _ = probes(binary,{"corpus":"swi","flags":["-fpack-struct=1"]},case,work,cc,cc)
            if accepted:
                failures.append(f"{cc}: packed compiler flags were ignored by the layout oracle")
        # Correct type annotations alone cannot detect an incorrect folded value.
        case.write_text("unsigned f(void){return (unsigned)-1 + 3u;}\n")
        value_probe = subprocess.run([binary,"probe",str(case)],capture_output=True,check=True).stdout
        probe = work / "constant-values.c"
        probe.write_bytes(value_probe)
        if not all(compiles(cc,probe) for cc in ("gcc","clang")):
            failures.append("correct integer constant value probes refused")
        changed = value_probe.replace(b"== 0x2ULL",b"== 0x3ULL")
        if changed == value_probe:
            failures.append("constant value mutation found no value check")
        probe.write_bytes(changed)
        if any(compiles(cc,probe) for cc in ("gcc","clang")):
            failures.append("incorrect integer constant value probe accepted")
        # value-losing mutations of emitted conversions change clang's IR
        sys.path.insert(0, str(ROOT / "tests"))
        from semantic_oracles import canonical_ir
        program = ROOT / "tests/semantics/conversions.c"

        def ir(source):
            path = work / "unit.i"
            path.write_bytes(source)
            return canonical_ir(subprocess.run(["clang", "-std=gnu11", "-w", "-O0", "-g0", "-S", "-emit-llvm", str(path), "-o", "-"],
                                               capture_output=True, check=True).stdout.decode())

        baseline = ir(program.read_bytes())
        if ir(subprocess.run([binary, "explicit", "--std=gnu11", "--dialect=clang", str(program)], capture_output=True,
                             check=True).stdout) != baseline:
            failures.append("explicit conversions changed the IR")
        mutations = 0
        while True:
            result = subprocess.run([binary, "explicit", "--std=gnu11", "--dialect=clang", f"--mutate={mutations+1}", str(program)],
                                    capture_output=True, check=True)
            if b"mutated" not in result.stderr:
                break
            mutations += 1
            if ir(result.stdout) == baseline:
                failures.append(f"mutation {mutations} left the IR unchanged")
        if mutations < 8:
            failures.append(f"only {mutations} mutations were exercised")
    for failure in failures:
        print("FAILED", failure)
    valid = sum(1 for _, _, v in CASES if v)
    print(f"semantic controls: {len(CASES) - valid} refusals and {valid} acceptances as both compilers decide; "
          f"{len(TARGETS)} data models each accepted only on its own target; {mutations} value-losing mutations detected")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
