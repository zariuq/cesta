"""Check distinctions that source-preserving presentations must keep."""

import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, expected=0):
    result = subprocess.run(list(map(str, arguments)), cwd=ROOT, capture_output=True)
    if result.returncode != expected:
        raise RuntimeError(f"{arguments}: {result.stderr.decode()}")
    return result.stdout


def main():
    output = ROOT / "build/presentation-semantics"
    output.mkdir(parents=True, exist_ok=True)
    cases = [
        ("anonymous_group", "struct{unsigned value;}a,b;void assign(void){a=b;}\n",
         {"StructDefinition": 1, "Declaration": 1}),
        ("prototype_distinction", "int f();int g(void);\n",
         {"OldFunctionDeclarator": 1, "FunctionDeclarator": 1}),
        ("old_definition", "int sum(a,b)int a;int b;{return a+b;}\n",
         {"OldFunctionDeclarator": 1, "NamedParameter": 0}),
        ("specifier_order", "const static unsigned a=1;static const unsigned b=2;\n", {}),
        ("attribute_placement", "unsigned a __attribute__((unused)),b __attribute__((aligned(16)));\n",
         {"Declaration": 1}),
        ("parameter_shadows_typedef", "typedef int T;int f(int T,int x){return T*x;}\n",
         {"Multiply": 1, "TypedefType": 0}),
        ("enum_shadows_typedef", "typedef int T;int f(void){enum{T=3};return T*2;}\n",
         {"Multiply": 1, "TypedefType": 0}),
        ("separate_namespaces", "typedef int T;struct{int T;}s;void f(void){goto T;T:s.T=1;}\n",
         {"Label": 1, "Goto": 1, "Member": 1}),
        ("nearest_else", "void f(int x,int y){if(x)if(y)x++;else y++;}\n",
         {"If": 1, "IfElse": 1}),
    ]
    records = []
    for name, text, counts in cases:
        directory = output / name
        baseline = directory / "native" / (name+".c")
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(text)
        tree = run([BINARY, "read", "--std=gnu11", baseline])
        for constructor, expected in counts.items():
            found = len(re.findall(rb'\(c:'+constructor.encode()+rb'(?=[ )])', tree))
            assert found == expected, (name, constructor, found, expected)
        native_objects = {}
        for compiler in ("gcc", "clang"):
            obj = directory / (compiler+"-native.o")
            run([compiler, "-std=gnu11", "-O3", "-g0", "-c", baseline, "-o", obj])
            native_objects[compiler] = obj.read_bytes()
        for presentation in ("ast/1", "compact-c/1", "compact-c/2", "cmera-style/1"):
            style = presentation.replace("/", "-")
            document = directory / (style+".cesta")
            document.write_bytes(run([BINARY, "view", "--std=gnu11", "--as", presentation, baseline]))
            printed = directory / style / baseline.name
            printed.parent.mkdir(exist_ok=True)
            printed.write_bytes(run([BINARY, "write", "--std=gnu11", document]))
            assert run([BINARY, "read", "--std=gnu11", printed]) == tree
            for compiler in ("gcc", "clang"):
                obj = directory / (compiler+"-"+style+".o")
                run([compiler, "-std=gnu11", "-O3", "-g0", "-c", printed, "-o", obj])
                assert obj.read_bytes() == native_objects[compiler]
        records.append({"case": name, "intended_constructor_counts": counts,
            "exact_presentations": 4, "matching_compilers": ["gcc", "clang"]})
    # Separating anonymous declarations changes type identity. It is a real
    # compiler rejection, rather than merely a different constructor spelling.
    separate = output / "anonymous-separated-negative.c"
    separate.write_text("struct{unsigned value;}a;struct{unsigned value;}b;void assign(void){a=b;}\n")
    for compiler in ("gcc", "clang"):
        run([compiler, "-std=gnu11", "-fsyntax-only", separate], expected=1)
    diagnostics = []
    for name, text, marker in [
        ("missing_range_end", "int a[4] = {[0 ... ] = 7};\n", "] = 7"),
        ("missing_conditional", "int f(int x){return x ?: ;}\n", ";}"),
        ("missing_range_start", "int a[4] = {[ ... 3] = 7};\n", "..."),
    ]:
        source = output / (name+".c")
        source.write_text(text)
        expected_offset = text.index(marker)
        result = subprocess.run([str(BINARY), "read", "--std=gnu11", str(source)], cwd=ROOT, capture_output=True)
        assert result.returncode == 1
        assert (f": byte {expected_offset}: ").encode() in result.stderr, result.stderr
        for compiler in ("gcc", "clang"):
            run([compiler, "-std=gnu11", "-fsyntax-only", source], expected=1)
        diagnostics.append({"case": name, "byte": expected_offset, "both_compilers_reject": True})
    (output / "summary.json").write_text(json.dumps({"distinctions": records,
        "anonymous_type_negative": True, "GNU_diagnostic_positions": diagnostics}, indent=2)+"\n")
    print("presentation semantics: nine declaration/binding/order distinctions across four styles and both compilers; anonymous-type negative and three GNU positions passed")


if __name__ == "__main__":
    main()
