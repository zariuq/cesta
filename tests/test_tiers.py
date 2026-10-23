"""Test tiers: check within test within qualify, and no earlier gate dropped."""

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent

# Every gate that `make check` or `make test` required before the tiers.
PREVIOUS = """
test-build-dependencies test-c test-c11parser test-compact-examples test-compiler-builds test-conflict-admission
test-corpora test-coverage test-c-printer test-driver test-examples test-foundation test-gnu-extensions
test-grammar-trees test-lean test-literal-bytes test-macro-admission test-macro-definition-ports test-macro-engine
test-macro-narrowing test-macro-native-names test-macro-port-admission test-macro-prescan test-macro-probes
test-macro-sizes test-macro-types test-macro-worklists test-metadata test-modules test-mutants test-native-authoring
test-petta test-portable-readers test-pp-core test-pp-corpora test-presentation-admission test-presentation-corpora
test-presentation-coverage test-presentation-driver test-presentations test-presentation-semantics
test-provenance-core test-provenance-corpora test-qualification-controls test-reference
test-schema-extension-admission test-semantic-corpora test-semantics test-source test-source-core
test-source-driver test-type-admission test-unicode-names test-whole-program-trees test-wire test-workbench
""".split()

# Gates that read external corpora or build Lean belong only to qualify.
QUALIFICATION_ONLY = """
test-corpora test-c11parser test-lean test-macro-admission test-macro-port-admission test-metadata
test-conflict-admission test-schema-extension-admission test-type-admission test-presentation-admission
test-presentation-corpora test-semantic-corpora test-pp-corpora test-provenance-corpora test-mutants test-petta
test-portable-readers test-workbench test-source test-export-counters-cetta test-export-admission
test-layout-admission test-layout-cetta test-layout-sanitizers test-layout-bridge test-layout-model
test-operation-audit test-operation-model test-operations test-operation-proposal
test-table-model test-table-admission test-operation-protocol test-protocol-model test-protocol-admission
test-checked-model test-checked-admission test-checked-sanitizers test-checked-generated
""".split()


def main():
    database = subprocess.run(["make", "-rpn", "-f", "Makefile", "test-wire"], cwd=ROOT,
                              capture_output=True, text=True).stdout
    tiers = {}
    for name in ("check", "test", "qualify"):
        lines = [line for line in database.splitlines() if line.startswith(name + ":")]
        assert len(lines) == 1, f"{name}: expected one consolidated rule, found {len(lines)}"
        tiers[name] = lines[0].split(":", 1)[1].split()
    targets = set(re.findall(r"^(test-[a-z0-9-]+):", database, re.M))
    check, test, qualify = (set(tiers[n]) for n in ("check", "test", "qualify"))
    assert check <= test <= qualify, "tiers must nest: check within test within qualify"
    missing = sorted(set(PREVIOUS) - qualify)
    assert not missing, f"qualify lost previous gates: {missing}"
    orphans = sorted(targets - qualify)
    assert not orphans, f"gates outside every tier: {orphans}"
    heavy = sorted(check & set(QUALIFICATION_ONLY)) + sorted(test & set(QUALIFICATION_ONLY))
    assert not heavy, f"corpus or Lean gates outside qualify: {heavy}"
    undefined = sorted(qualify - targets)
    assert not undefined, f"tiers name undefined gates: {undefined}"
    print(f"test tiers: check {len(check)}, test {len(test)}, qualify {len(qualify)} gates; "
          f"{len(PREVIOUS)} previous gates retained", flush=True)


if __name__ == "__main__":
    main()
