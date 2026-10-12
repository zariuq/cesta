"""Check source layout, precedence and compiler-sensitive grouping independently."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, *, expected=0):
    result = subprocess.run(list(map(str, arguments)), cwd=ROOT, capture_output=True)
    if result.returncode != expected:
        raise RuntimeError(f"{arguments}: {result.stderr.decode()}")
    return result.stdout


def main():
    output = ROOT / "build/c-printer"
    output.mkdir(parents=True, exist_ok=True)
    expressions = [
        ("sum", "a+b", "a + b"),
        ("product", "a+b*c", "a + b * c"),
        ("precedence", "a*(b+c)", "a * (b + c)"),
        ("right_tree", "a+(b+c)", "a + (b + c)"),
        ("assignment", "a=b", "a = b"),
        ("equal", "a==b", "a == b"),
        ("shift", "a<<b", "a << b"),
        ("bitwise", "a&b", "a & b"),
        ("logical", "(a&&b)||c", "(a && b) || c"),
        ("shift_sum", "a<<(b+c)", "a << (b + c)"),
        ("sum_shift", "(a+b)<<c", "(a + b) << c"),
        ("bitwise_sum", "(a+b)|c", "(a + b) | c"),
        ("bitwise_compare", "(a==b)&c", "(a == b) & c"),
        ("bitwise_relation", "(a<b)|c", "(a < b) | c"),
        ("bitwise_nested", "(a&b)|c", "(a & b) | c"),
        ("xor_nested", "(a&b)^c", "(a & b) ^ c"),
        ("chained_relation", "(a<b)<c", "(a < b) < c"),
        ("chained_equal", "(a==b)==c", "(a == b) == c"),
    ]
    records = []
    for name, expression, spelling in expressions:
        source = output / "baseline" / (name+".c")
        source.parent.mkdir(exist_ok=True)
        source.write_text("unsigned f(unsigned a,unsigned b,unsigned c){(void)c;return "+expression+";}\n")
        ast = output / (name+".csexp")
        ast.write_bytes(run([BINARY, "read", "--std=c11", source]))
        printed = output / "printed" / source.name
        printed.parent.mkdir(exist_ok=True)
        printed.write_bytes(run([BINARY, "write", "--std=c11", ast]))
        assert ("return "+spelling+";").encode() in printed.read_bytes(), printed.read_text()
        assert b"{\n  " in printed.read_bytes() and printed.read_bytes().count(b"\n") >= 3
        assert run([BINARY, "read", "--std=c11", printed]) == ast.read_bytes()
        normalized = output / (name+".normalized.csexp")
        normalized.write_bytes(run([BINARY, "read", "--std=c11", printed]))
        assert run([BINARY, "write", "--std=c11", normalized]) == printed.read_bytes()
        for compiler in ("gcc", "clang"):
            objects = []
            for label, current in (("native", source), ("printed", printed)):
                obj = output / (compiler+"-"+name+"-"+label+".o")
                run([compiler, "-std=c11", "-O3", "-g0", "-Wall", "-Wextra", "-Werror", "-c", current, "-o", obj])
                objects.append(obj.read_bytes())
            assert objects[0] == objects[1]
        records.append({"case": name, "exact_tree": True, "idempotent": True,
                        "matching_objects": ["gcc", "clang"], "expression": spelling})
    controls = output / "control.c"
    controls.write_text("unsigned f(unsigned x,unsigned y){if((x=y))return x;while((x=y)){return x;}"
                        "for(x=y;(x=y);x=y){return x;}return x=y;}\n")
    tree = output / "control.csexp"
    tree.write_bytes(run([BINARY, "read", "--std=c11", controls]))
    printed = output / "control-printed.c"
    printed.write_bytes(run([BINARY, "write", "--std=c11", tree]))
    assert b"if ((x = y))" in printed.read_bytes()
    assert b"while ((x = y))" in printed.read_bytes()
    assert b"for (x = y; (x = y); x = y)" in printed.read_bytes()
    assert b"return x = y;" in printed.read_bytes()
    assert run([BINARY, "read", "--std=c11", printed]) == tree.read_bytes()
    for compiler in ("gcc", "clang"):
        run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", printed])
    lexical = output / "lexical-boundary.c"
    lexical.write_text("void f(void){1 . member;1.0 . member;}\n")
    lexical_tree = output / "lexical-boundary.csexp"
    lexical_tree.write_bytes(run([BINARY, "read", "--std=c11", lexical]))
    lexical_printed = output / "lexical-boundary-printed.c"
    lexical_printed.write_bytes(run([BINARY, "write", "--std=c11", lexical_tree]))
    assert b"1 .member" in lexical_printed.read_bytes() and b"1.0 .member" in lexical_printed.read_bytes()
    assert run([BINARY, "read", "--std=c11", lexical_printed]) == lexical_tree.read_bytes()
    # These syntax trees are deliberately ill-typed; layout preserves syntax,
    # and native compilers continue to reject their member accesses.
    for compiler in ("gcc", "clang"):
        run([compiler, "-std=c11", "-fsyntax-only", lexical_printed], expected=1)
    (output / "summary.json").write_text(json.dumps({"expressions": records,
        "control_assignment_contexts": True, "lexical_boundary_preservation": True,
        "data_driven_layout": True}, indent=2)+"\n")
    print(f"C printer: {len(records)} precedence/grouping cases; exact trees, readable layout, idempotence and matching warning-clean objects; assignment contexts passed")


if __name__ == "__main__":
    main()
