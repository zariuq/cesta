"""Compiler evidence for conversions that cross qualification or storage boundaries."""

import subprocess
import tempfile
from pathlib import Path

import semantic_oracles as oracle

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        work = Path(temporary)
        source = work / "unit.i"
        source.write_bytes((ROOT / "tests/semantics/conversion_boundaries.c").read_bytes())
        unit = {"corpus": "swi", "flags": ["-std=gnu11", "-w"]}
        for compiler in ("gcc", "clang"):
            accepted, counts, diagnostic = oracle.probes(BINARY, unit, source, work, compiler, compiler)
            if not accepted:
                raise RuntimeError(f"{compiler} boundary probes failed: {diagnostic}")
        tree = oracle.clang_tree(source, unit)
        details = []
        conversions = oracle.categories_and_conversions(BINARY, unit, source, tree, details=details)
        if conversions.get("disagree") or conversions.get("unmatched"):
            raise RuntimeError(f"conversion boundaries differ: {conversions}: {details}")
        evidence = oracle.explicit(BINARY, unit, source, work / "explicit")
        if not evidence["identical_ir"] or not evidence["identical_object"]:
            raise RuntimeError(f"explicit boundary casts changed the program: {evidence}")
        # A mutation that treats a pointer as an integer is refused by Cesta
        # and by both compilers rather than silently changing the ABI.
        bad = work / "bad.i"
        bad.write_text("struct X { int n; }; struct X f(void *p) { return p; }\n")
        for compiler in ("gcc", "clang"):
            native = subprocess.run([compiler, "-std=gnu11", "-Werror", "-fsyntax-only", str(bad)], capture_output=True)
            cesta = subprocess.run([str(BINARY), "types", "--dialect="+compiler, str(bad)], capture_output=True)
            if native.returncode == 0 or cesta.returncode == 0:
                raise RuntimeError(f"{compiler} accepted an incompatible pointer/record return")
    print(f"conversion boundaries: two compiler probe sets; {conversions['agree']} conversion chains; "
          f"{evidence['casts']} explicit casts preserve IR and objects; two incompatible-return refusals")


if __name__ == "__main__":
    main()
