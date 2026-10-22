"""Operation contracts: a declared table is read, checked and normalized; malformed declarations are
refused with their reasons; the Lean kernel decides that the printed table keeps the laws of
OperationContracts.tableOk, and refuses printed tables that break them."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from export_admission import LEAN, atom, kernel  # noqa: E402
from lean_contracts import OPERATION_MODULES  # noqa: E402
from oracles.pro_compact_cesta import Sym, Text, parse  # noqa: E402

BINARY = ROOT / "build/cesta"
MODULE = ROOT / "tests/operations/Example.module"
WORK = ROOT / "build/operations"
LAWS = ["Mettapedia.GSLT.LanguageDef.OperationContracts.Footprint.independent_iff",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.Footprint.independent_iff_separate",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.Footprint.pure_independent",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.concrete_independent",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.receipt_view_swap_of_declared",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.independence_does_not_order_streams",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.admitted_laws",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.laws_of_admitted",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.pureRow_independent",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.queryEffect_pure",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.queryEffect_writer",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.queryEffect_unknown",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.query_row_spec",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.speculation_rows_not_writers",
        "Mettapedia.GSLT.LanguageDef.OperationContracts.rows_of_admitted"]

# (text replaced once, replacement, part of the expected refusal)
MUTANTS = [
    ('(Spelling "space-push") (Symbol space_push) (Dispatch "special")\n        (Forms (Form (Arg "value" "data" "borrow") (Arg "value" "data" "consume")))\n        (Footprint (Reads) (Writes space))\n        (Context (Blocks "no") (Branches "no") (Progress "single") (Charges))\n        (Result "shared")\n        (Declared (Static "no") (TypePure "no") (Speculative "no")',
     '(Spelling "space-push") (Symbol space_push) (Dispatch "special")\n        (Forms (Form (Arg "value" "data" "borrow") (Arg "value" "data" "consume")))\n        (Footprint (Reads) (Writes space))\n        (Context (Blocks "no") (Branches "no") (Progress "single") (Charges))\n        (Result "shared")\n        (Declared (Static "no") (TypePure "no") (Speculative "yes")',
     "an operation that writes is not admitted under speculation"),
    ('(Spelling "size") (Symbol size) (Dispatch "grounded")\n        (Forms (Form (Arg "value" "data" "borrow")))\n        (Footprint (Reads space) (Writes))\n        (Context (Blocks "no") (Branches "no") (Progress "single") (Charges))\n        (Result "fresh")\n        (Declared (Static "yes") (TypePure "no")',
     '(Spelling "size") (Symbol size) (Dispatch "grounded")\n        (Forms (Form (Arg "value" "data" "borrow")))\n        (Footprint (Reads space) (Writes))\n        (Context (Blocks "no") (Branches "no") (Progress "single") (Charges))\n        (Result "fresh")\n        (Declared (Static "yes") (TypePure "yes")',
     "a type-level operation declares no read or write"),
    ("(Footprint (Reads) (Writes world))", "(Footprint (Reads) (Writes stdout))", "Writes.value value stdout names no row in Resources.name"),
    ("(Charges transition))", "(Charges transfer))", "OpCharges.value value transfer names no row in Charges.name"),
    ("(Profiles prime));", "(Profiles hol));", "OpProfiles.value value hol names no row in Profiles.name"),
    ('(Spelling "size")', '(Spelling "match")', "duplicate row spelling"),
    ("(size) ==", "(match) ==", "duplicate row name"),
    ('(Forms (Form (Arg "atom" "data" "borrow")))', '(Forms (Form (Arg "atom" "data" "borrow")) (Form (Arg "value" "data" "borrow")))',
     "two forms take 1 arguments"),
    ('(Replay "refused" (Through 1))', '(Replay "refused" (Through 2))', "replay passes through a form the operation does not have"),
    ('(Arg "atom" "pattern" "borrow")', '(Arg "atom" "glob" "borrow")', "argument demand is not one of the admitted words"),
    ('(Result "borrowed")', '(Result "leased")', "result is not one of the admitted words"),
    ('(Progress "bounded") (Charges transition)', '(Progress "eventually") (Charges transition)', "progress is not one of"),
    ('(Blocks "yes")', '(Blocks "maybe")', "Blocks is \"yes\", \"no\" or \"unknown\""),
    ('(Footprint (Reads space bindings) (Writes))', '(Footprint (Reads space space) (Writes))', "Reads names space twice"),
    ('        (Result "fresh")\n        (Declared (Static "no") (TypePure "no") (Speculative "no") (Replay "refused"))\n        (Profiles prime));',
     '        (Declared (Static "no") (TypePure "no") (Speculative "no") (Replay "refused"))\n        (Profiles prime));',
     "operation does not declare Result"),
    ('(Resource bindings "scratch")', '(Resource bindings "cache")', "resource is (Resource NAME"),
    ('(Resource ledger "store")', '(Resource ledger "store") (Resource ledger "store")', "duplicate row name"),
    ('(Symbol op_plus)', '(Symbol op-plus)', "LR syntax rejected"),
    ('(Symbol op_plus)', '(Symbol "op plus")', "symbol is (Symbol C-IDENTIFIER)"),
    ('(Declared (Static "yes") (TypePure "yes")', '(Declared (Static "yes") (TypePure "no")',
     "a pure grounded operation has the type-level licence"),
    ('(Symbol quote) (Dispatch "special")', '(Symbol quote) (Dispatch "macro")', "dispatch is not one of the admitted words"),
    ('(Symbol size) (Dispatch "grounded")', '(Symbol match) (Dispatch "grounded")', "duplicate row symbol"),
    ('(Footprint "unknown")', '(Footprint "pure")', "footprint is (Footprint (Reads ...) (Writes ...)) or (Footprint \"unknown\")"),
    ('(Blocks "unknown")', '(Blocks "perhaps")', "Blocks is \"yes\", \"no\" or \"unknown\""),
]


def check(module, success=True):
    result = subprocess.run([str(BINARY), "operations", "check", str(module), "Operations"], capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"status {result.returncode}\n{result.stderr.decode(errors='replace')}")
    return result


def model_snapshot(extra=()):
    records = {}
    for name in [*OPERATION_MODULES, *extra]:
        relative = name.replace(".", "/")
        source = LEAN / (relative + ".lean")
        artifact = LEAN / ".lake/build/lib/lean" / (relative + ".olean")
        if not artifact.exists() or artifact.stat().st_mtime_ns < source.stat().st_mtime_ns:
            raise RuntimeError(f"operation model is not freshly built: {name}; run make test-operation-model")
        records[name] = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "olean_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    return records


def shared_projection(records):
    """Independently project the recorded domain facts into the shared schema."""
    def quoted(value):
        return Text(str(value).encode())

    def table(name, columns, keys, rows):
        return [Sym("Table"), Sym(name), [Sym("Columns"), *[
            [Sym("Column"), Sym(column), kind] for column, kind in columns]],
            *[[Sym(head), *map(Sym, key)] for head, key in keys],
            [Sym("Rows"), *[[Sym("Row"), *row] for row in rows]]]

    vocabulary, operations = records[0], records[1:]
    result = [Sym("cesta-tables/1"), table("Resources",
        [("name", Sym("Text")), ("kind", [Sym("Enum"), Sym("store"), Sym("scratch")])],
        [("Key", ["name"])], [[quoted(name), Sym(kind.data.decode())] for name, kind in vocabulary[2][1:]])]
    for name, entries in (("Charges", vocabulary[3][1:]), ("Profiles", vocabulary[4][1:])):
        result.append(table(name, [("name", Sym("Text"))], [("Key", ["name"])], [[quoted(x)] for x in entries]))
    result.append(table("Operations", [("name", Sym("Text")), ("symbol", Sym("Identifier")), ("spelling", Sym("Text"))],
        [("Key", ["name"]), ("Unique", ["symbol"]), ("Unique", ["spelling"])],
        [[quoted(op[1]), op[3], op[2]] for op in operations]))
    for index, (name, target) in enumerate((("Reads", "Resources"), ("Writes", "Resources"),
                                            ("OpCharges", "Charges"), ("OpProfiles", "Profiles"))):
        rows = []
        for op in operations:
            entries = (op[6][index+1][1:] if len(op[6]) == 3 else []) if index < 2 else \
                      op[7][4][1:] if index == 2 else op[10][1:]
            rows += [[quoted(op[1]), quoted(value)] for value in entries]
        result.append(table(name, [("operation", [Sym("Ref"), Sym("Operations"), Sym("name")]),
                                   ("value", [Sym("Ref"), Sym(target), Sym("name")])],
                            [("Key", ["operation", "value"])], rows))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-memory-mb", type=int)
    args = parser.parse_args()
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    first, again = check(MODULE), check(MODULE)
    assert first.stdout == again.stdout, "normalized output differs between runs"
    (WORK / "Example.operations").write_bytes(first.stdout)
    records = parse(first.stdout)
    assert str(records[0][0]) == "cesta-operation-vocabulary/1"
    assert all(str(r[0]) == "cesta-operation/1" for r in records[1:])
    shared_text = subprocess.run([str(BINARY), "operations", "tables", str(MODULE), "Operations"],
                                 capture_output=True, check=True).stdout
    shared, = parse(shared_text)
    expected_shared = shared_projection(records)
    assert shared == expected_shared, "shared operation rows differ from the independently recorded domain facts"
    (WORK / "Example.tables").write_bytes(shared_text)

    source = MODULE.read_text()
    for index, (old, new, expected) in enumerate(MUTANTS):
        assert source.count(old) == 1, f"mutant {index}: text not found once"
        mutant = WORK / f"mutant{index}.module"
        mutant.write_text(source.replace(old, new, 1))
        message = check(mutant, success=False).stderr.decode()
        assert expected in message, f"mutant {index}: expected {expected!r}, got {message!r}"
    no_vocabulary = WORK / "novocabulary.module"
    start = source.index("      (Vocabulary) ==")
    no_vocabulary.write_text(source[:start] + source[source.index("(op_plus) ==") - 6:])
    assert "declares no vocabulary" in check(no_vocabulary, success=False).stderr.decode()

    # The header and the MeTTa fragment are printed from the same rows.
    header = WORK / "operations.h"
    subprocess.run([str(BINARY), "operations", "header", str(MODULE), "Operations", "-o", str(header)], check=True)
    rows_text = subprocess.run([str(BINARY), "operations", "rows", str(MODULE), "Operations"], capture_output=True,
                               check=True).stdout.decode()
    lists = [("Static", "STATIC_FIELDS"), ("Pure", "PURE_FIELDS"), ("TypePure", "TYPE_PURE_FIELDS"),
             ("Speculative", "SPECULATIVE_FIELDS")]
    body = ['#include <stdio.h>', '#include "operations.h"', '#define L(f) printf(" %s", #f);',
            '#define Q(f, e) printf(" (%s %u)", #f, (unsigned)(e));', 'int main(void) {']
    for head, macro in lists:
        body.append(f'    printf(" ({head}"); CESTA_OPERATION_{macro}(L) printf(")");')
    body.append('    printf(" (Query"); CESTA_OPERATION_QUERY_EFFECT_ROWS(Q) printf(")");')
    body.append('    printf(" (Unknown"); CESTA_OPERATION_UNKNOWN_FOOTPRINT_FIELDS(L) printf(")\\n");')
    body.append("    return 0;\n}")
    probe = WORK / "probe.c"
    probe.write_text("\n".join(body) + "\n")
    expected = rows_text.replace("(cesta-operation-rows/1", "")[:-2] + "\n"
    for cc in ("gcc", "clang"):
        exe = WORK / f"probe-{cc}"
        subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", "-I", str(WORK), str(probe),
                        "-o", str(exe)], check=True)
        printed_rows = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        assert printed_rows == expected, (cc, printed_rows, expected)
    fragment = subprocess.run([str(BINARY), "operations", "metta", str(MODULE), "Operations"], capture_output=True,
                              text=True, check=True).stdout.splitlines()[1:]
    spellings = {str(r[1]): r[2].data.decode() for r in records[1:]}
    query, = [x for x in parse(rows_text.encode())[0][1:] if str(x[0]) == "Query"]
    assert fragment == [f"(query-head {spellings[str(q[0])]} {'world-write' if str(q[1]) == '2' else 'relational-query'})"
                        for q in query[1:]], fragment

    # The kernel decides the printed table, and refuses printed tables that break its laws.
    extra_models = ["Mettapedia.GSLT.LanguageDef.DeclaredTables"]
    models = model_snapshot(extra_models)
    binary_digest = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    lines = ["import Mettapedia.GSLT.LanguageDef.OperationContracts",
             "import Mettapedia.GSLT.LanguageDef.DeclaredTables",
             "open Mettapedia.GSLT.LanguageDef.MacroExportPlan Mettapedia.GSLT.LanguageDef.OperationContracts",
             "set_option Elab.async false", "set_option maxRecDepth 4096", "namespace CestaOperationAdmission",
             f"def vocabulary : Atom := {atom(records[0])}"]
    lines += [f"def shared : Atom := {atom(shared)}",
              f"def expectedShared : Atom := {atom(expected_shared)}",
              "theorem sharedAdmitted : Mettapedia.GSLT.LanguageDef.DeclaredTables.checkSource shared = true := by decide +kernel",
              "theorem sharedProjectionExact : Mettapedia.GSLT.LanguageDef.DeclaredTables.decodeBundle shared = Mettapedia.GSLT.LanguageDef.DeclaredTables.decodeBundle expectedShared := by decide +kernel"]
    swapped = shared_projection(records)
    swapped[5][-1], swapped[6][-1] = swapped[6][-1], swapped[5][-1]
    lines += [f"def swappedRoles : Atom := {atom(swapped)}",
              "theorem swappedRolesStillTyped : Mettapedia.GSLT.LanguageDef.DeclaredTables.checkSource swappedRoles = true := by decide +kernel",
              "theorem swappedRolesNotSource : Mettapedia.GSLT.LanguageDef.DeclaredTables.decodeBundle swappedRoles ≠ Mettapedia.GSLT.LanguageDef.DeclaredTables.decodeBundle expectedShared := by decide +kernel"]
    names = []
    for k, r in enumerate(records[1:]):
        lines.append(f"def operation{k} : Atom := {atom(r)}")
        names.append(f"operation{k}")
    table = "[" + ", ".join(names) + "]"
    lines.append(f"theorem admitted : tableAdmitted vocabulary {table} = true := by decide")
    printed = first.stdout.decode()

    def printed_mutant(name, old, new):
        assert printed.count(old) == 1, f"control {name}: text not found once"
        mutated = parse(printed.replace(old, new, 1).encode())
        defs = [f"def {name}Vocabulary : Atom := {atom(mutated[0])}"]
        ops = []
        for k, r in enumerate(mutated[1:]):
            defs.append(f"def {name}{k} : Atom := {atom(r)}")
            ops.append(f"{name}{k}")
        lines.extend(defs)
        lines.append(f"theorem {name}Refused : tableAdmitted {name}Vocabulary [{', '.join(ops)}] = false := by decide")

    printed_mutant("speculativeWriter", '(Writes space)) (Context "no" "no" "single" (Charges)) (Result "shared") '
                   '(Declared "no" "no" "no"', '(Writes space)) (Context "no" "no" "single" (Charges)) '
                   '(Result "shared") (Declared "no" "no" "yes"')
    printed_mutant("pureWithoutTypeLicence", '(Declared "yes" "yes" "no"', '(Declared "yes" "no" "no"')
    printed_mutant("typeLevelReader", '(Reads space) (Writes)) (Context "no" "no" "single" (Charges)) '
                   '(Result "fresh") (Declared "yes" "no"', '(Reads space) (Writes)) (Context "no" "no" '
                   '"single" (Charges)) (Result "fresh") (Declared "yes" "yes"')
    printed_mutant("undeclaredResource", "(Writes world)", "(Writes stdout)")
    printed_mutant("duplicateSpelling", '"size" size', '"match" size')
    # The derived rows: admitted as recorded, refused with one pure row dropped or an unknown footprint
    # read as pure.
    rows_text = subprocess.run([str(BINARY), "operations", "rows", str(MODULE), "Operations"], capture_output=True,
                               check=True).stdout
    (WORK / "Example.rows").write_bytes(rows_text)
    rows, = parse(rows_text)
    lines.append(f"def rows : Atom := {atom(rows)}")
    lines.append(f"theorem rowsRecorded : rowsAdmitted vocabulary {table} rows = true := by decide")
    for name, old, new in (("droppedPure", b"(Pure op_plus)", b"(Pure)"),
                           ("unknownAsPure", b"(Pure op_plus)", b"(Pure op_plus py_call)")):
        assert rows_text.count(old) == 1, name
        bad, = parse(rows_text.replace(old, new, 1))
        lines.append(f"theorem {name}Refused : rowsAdmitted vocabulary {table} ({atom(bad)}) = false := by decide")
    lines.append("end CestaOperationAdmission")
    decisions = ["CestaOperationAdmission." + re.match(r"theorem ([A-Za-z0-9_]+)", line)[1]
                 for line in lines if line.startswith("theorem ")]
    lines += [f"#print axioms {law}" for law in LAWS]
    lines += [f"#print axioms {name}" for name in decisions]
    output = kernel(WORK / "admission.lean", lines, args.lean_memory_mb)
    dependencies = {}
    for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]", output, re.DOTALL):
        found = {x.strip() for x in match[2].split(",") if x.strip()}
        if not found <= {"propext", "Classical.choice", "Quot.sound"}:
            raise RuntimeError("unexpected operation proof axioms: " + str(found))
        dependencies[match[1]] = sorted(found)
    for name in re.findall(r"'([^']+)' does not depend on any axioms", output):
        dependencies[name] = []
    if set(dependencies) != set(LAWS + decisions):
        raise RuntimeError("axiom audit did not account for every law and decision")
    if model_snapshot(extra_models) != models or hashlib.sha256(BINARY.read_bytes()).hexdigest() != binary_digest:
        raise RuntimeError("Lean model or Cesta binary changed during admission")
    (WORK / "summary.json").write_text(json.dumps({"operations": len(records) - 1, "mutants": len(MUTANTS) + 1,
        "laws": LAWS, "decisions": decisions, "axioms": dependencies, "cesta_sha256": binary_digest,
        "lean_models": models}, indent=2) + "\n")
    print(f"operations: {len(records) - 1} declared operations read and normalized; {len(MUTANTS) + 1} malformed "
          "declarations refused with their reasons; the kernel admits the printed table and refuses a speculative "
          "writer, a pure operation without the type-level licence, a type-level reader, an undeclared resource and "
          "a duplicate spelling; the derived rows are admitted as recorded, printed alike in the header (gcc and "
          "clang) and the MeTTa fragment, and refused with a dropped pure row or an unknown footprint read as pure; "
          f"{len(decisions)} decisions "
          f"and {len(LAWS)} laws axiom-audited")


if __name__ == "__main__":
    main()
