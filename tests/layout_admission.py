"""Layout admission: the Lean kernel decides that every declared layout is well formed, so the
generic layout laws hold of it, and that the declared frame identities are the layout the Lean side
states for CeTTa (CeTTaFrameLayout proves that layout is CeTTa's codec)."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from export_admission import LEAN, atom, kernel  # noqa: E402
from lean_contracts import LAYOUT_MODULES  # noqa: E402
from oracles.pro_compact_cesta import parse  # noqa: E402

BINARY = ROOT / "build/cesta"
MODULE = ROOT / "tests/layouts/CeTTa.module"
THEORIES = ["Frames", "AtomsSplit", "FramesWide"]
LAYOUT_SOURCES = [(MODULE, theory) for theory in THEORIES] + [
    (ROOT / "tests/layouts/Concurrent.module", "Counters"),
] + [(ROOT / "tests/layouts/Edges.module", theory) for theory in ("Widths", "Many", "Deep")] + [
    (ROOT / "layouts/CeTTa.module", theory) for theory in ("Variables", "Frames")
] + [(ROOT / "tests/layouts/Symbols.module", "Names")]
WORK = ROOT / "build/layout-admission"
LAWS = ["Mettapedia.GSLT.LanguageDef.LayoutCodes.Code.decode_encode",
        "Mettapedia.GSLT.LanguageDef.LayoutCodes.Code.encode_decode",
        "Mettapedia.GSLT.LanguageDef.LayoutCodes.Code.parts_injective",
        "Mettapedia.GSLT.LanguageDef.LayoutDeclarations.facts_of_admitted",
        "Mettapedia.GSLT.LanguageDef.LayoutDeclarations.metadata_of_admitted",
        "Mettapedia.GSLT.LanguageDef.LayoutKinds.flags_tile",
        "Mettapedia.GSLT.LanguageDef.LayoutKinds.summary_any",
        "Mettapedia.GSLT.LanguageDef.LayoutKinds.summary_all",
        "Mettapedia.GSLT.LanguageDef.LayoutKinds.summary_occurrence",
        "Mettapedia.GSLT.LanguageDef.LayoutKinds.store_drops_occurrence",
        "Mettapedia.GSLT.LanguageDef.LayoutKinds.copy_keeps",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.lowering_of_admitted",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.eval_parts",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.eval_valid_code",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.eval_encode",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.eval_set",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.fold_derive",
        "Mettapedia.GSLT.LanguageDef.LayoutLowering.eval_apply",
        "Mettapedia.GSLT.LanguageDef.LayoutMigration.migration_of_admitted",
        "Mettapedia.GSLT.LanguageDef.LayoutMigration.migrate_injective",
        "Mettapedia.GSLT.LanguageDef.LayoutBitVec.eval_toNat"]
# Declared layouts the Lean side already states independently.
KNOWN = {"FrameIdentity": "Mettapedia.Languages.MeTTa.Bridges.GSLT.CeTTaFrameLayoutCode.frameIdentity"}


def model_snapshot():
    records = {}
    for name in LAYOUT_MODULES:
        relative = name.replace(".", "/")
        source = LEAN / (relative + ".lean")
        artifact = LEAN / ".lake/build/lib/lean" / (relative + ".olean")
        if not artifact.exists() or artifact.stat().st_mtime_ns < source.stat().st_mtime_ns:
            raise RuntimeError(f"layout model is not freshly built: {name}; run make test-layout-model")
        records[name] = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "olean_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-memory-mb", type=int)
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    binary_digest = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    models = model_snapshot()
    modules = {str(module.relative_to(ROOT)): hashlib.sha256(module.read_bytes()).hexdigest()
               for module, _ in LAYOUT_SOURCES}
    lines = ["import Mettapedia.GSLT.LanguageDef.LayoutMigration",
             "import Mettapedia.GSLT.LanguageDef.LayoutBitVec",
             "import Mettapedia.Languages.MeTTa.Bridges.GSLT.CeTTaFrameLayoutCode",
             "open Mettapedia.GSLT.LanguageDef.MacroExportPlan Mettapedia.GSLT.LanguageDef.LayoutDeclarations",
             "open Mettapedia.GSLT.LanguageDef.LayoutKinds Mettapedia.GSLT.LanguageDef.LayoutLowering",
             "open Mettapedia.GSLT.LanguageDef.LayoutMigration",
             "set_option Elab.async false",
             "set_option maxRecDepth 4096",
             "namespace CestaLayoutAdmission"]
    report = []
    for module, theory in LAYOUT_SOURCES:
        label = theory if module == MODULE else f"{module.stem}_{theory}"
        printed = subprocess.run([str(BINARY), "layout", "check", str(module), theory],
                                 capture_output=True, check=True).stdout
        (WORK / f"{label}.layouts").write_bytes(printed)
        functions = subprocess.run([str(BINARY), "layout", "functions", str(module), theory],
                                   capture_output=True, check=True).stdout
        (WORK / f"{label}.functions").write_bytes(functions)
        records = parse(functions)
        recorded = [r for r in records if str(r[0]) == "cesta-layout-functions/1"]
        migrations = [r for r in records if str(r[0]) == "cesta-layout-migration/1"]
        layouts = parse(printed)
        if len(recorded) + len(migrations) != len(records):
            raise RuntimeError(f"{theory}: unknown generated record")
        if not layouts or any(str(d[0]) != "cesta-layout/1" for d in layouts):
            raise RuntimeError(f"{theory}: expected nonempty normalized layout records")
        if [str(d[1]) for d in layouts] != [str(f[1]) for f in recorded]:
            raise RuntimeError(f"{theory}: layouts/accessor records differ in count, order or names")
        if len({str(d[1]) for d in layouts}) != len(layouts):
            raise RuntimeError(f"{theory}: duplicate layout record")
        expected_migrations = [(str(meta[1]), str(d[1])) for d in layouts for meta in d[5:]
                               if str(meta[0]) == "From"]
        if expected_migrations != [(str(m[1]), str(m[2])) for m in migrations]:
            raise RuntimeError(f"{theory}: missing, extra or reordered migration records")
        first = len(report)
        for data, accessors in zip(layouts, recorded):
            name = str(data[1]); k = len(report)
            lines.append(f"def layout{k} : Atom := {atom(data)}")
            lines.append(f"theorem admitted{k} : admitted layout{k} = true := by decide")
            lines.append(f"def functions{k} : Atom := {atom(accessors)}")
            lines.append(f"theorem lowered{k} : functionsAdmitted layout{k} functions{k} = true := by decide")
            entry = {"module": str(module.relative_to(ROOT)), "theory": theory, "layout": name,
                     "admitted": True, "accessors": len(accessors) - 2}
            fields = {str(f[0]): k for k, f in enumerate(data[3][1:])}
            if not any(str(x[0]) == "Node" for x in [data[4]]):
                # A single-case word: its kinds are decided, so the derivation laws apply to it.
                lines.append(f"theorem flags{k} : ((decodeLayout layout{k}).bind flagsOf).isSome = true := by decide")
                entry["kinds"] = True
            if "he_evaluated" in fields:
                lines.append(f"theorem evaluatedMark{k} : ((decodeLayout layout{k}).bind flagsOf).map "
                             f"(fun f => kindAt f 31) = some (some .occurrence) := by decide")
                entry["occurrence_bit"] = 31
            if name in KNOWN:
                lines.append(f"theorem same{k} : (decodeLayout layout{k}).map (·.code) = some {KNOWN[name]} := rfl")
                entry["equals"] = KNOWN[name]
            report.append(entry)
        index = {report[j]["layout"]: j for j in range(first, len(report))}
        for m in migrations:
            old, new = index[str(m[1])], index[str(m[2])]
            lines.append(f"def migration{new} : Atom := {atom(m)}")
            lines.append(f"theorem migrated{new} : migrationAdmitted layout{old} layout{new} migration{new} = true "
                         ":= by decide")
            report[new]["migrates_from"] = str(m[1])
    # A printed layout whose generation overlaps the handle is decided not admitted.
    frames = (WORK / "Frames.layouts").read_bytes()
    assert frames.count(b"(Place 20 11 0 1 2046)") == 1
    overlapping, = [d for d in parse(frames.replace(b"(Place 20 11 0 1 2046)", b"(Place 19 11 0 1 2046)"))
                    if str(d[1]) == "FrameIdentity"]
    lines.append(f"theorem overlapRefused : admitted ({atom(overlapping)}) = false := by decide")
    # An accessor whose mask differs from the lowering by one bit is refused.
    accessors = (WORK / "Frames.functions").read_bytes()
    assert accessors.count(b"(lit 1048575)") >= 1
    mutated, = [f for f in parse(accessors.replace(b"(lit 1048575)", b"(lit 1048574)", 1)) if str(f[1]) == "FrameIdentity"]
    lines.append(f"theorem maskRefused : functionsAdmitted layout0 ({atom(mutated)}) = false := by decide")
    # A migration whose old reading of a handle has a narrower mask is refused.
    line, = [x for x in (WORK / "FramesWide.functions").read_bytes().splitlines()
             if x.startswith(b"(cesta-layout-migration/1")]
    bad, = parse(line.replace(b"(lit 1048575)", b"(lit 1048574)", 1))
    new = next(j for j, e in enumerate(report) if e["layout"] == "FrameIdentityWide")
    old = next(j for j, e in enumerate(report) if e["layout"] == "FrameIdentity" and e["theory"] == "FramesWide")
    lines.append(f"theorem migrationRefused : migrationAdmitted layout{old} layout{new} ({atom(bad)}) = false := by decide")
    # Metadata has its own checks: a claimed write discipline, alias or bound
    # must be valid even when the word's field algebra is otherwise sound.
    flags = (WORK / "AtomsSplit.layouts").read_bytes()
    metadata_mutants = [
        ("locationRefused", b'(Location "construction")', b'(Location "unknown")'),
        ("kindRefused", b'"any-child"', b'"unknown"'),
        ("requirementRefused", b'(var_bloom 24 ', b'(var_bloom 21 '),
        ("aliasRefused", b'(term_stable hash_stable ', b'(term_stable missing_field '),
    ]
    for name, before, after in metadata_mutants:
        if before not in flags:
            raise RuntimeError(f"metadata control spelling changed: {name}")
        data, = [d for d in parse(flags.replace(before, after, 1)) if str(d[1]) == "AtomFlags"]
        lines.append(f"theorem {name} : admitted ({atom(data)}) = false := by decide")
    cache, = [d for d in parse(flags) if str(d[1]) == "AtomHashValid"]
    cache = list(cache)
    cache[5] = parse(b'(Location "construction")')[0]
    lines.append(f"theorem publicationRefused : admitted ({atom(cache)}) = false := by decide")
    lines.append("end CestaLayoutAdmission")
    decisions = ["CestaLayoutAdmission." + re.match(r"theorem ([A-Za-z0-9_]+)", line)[1]
                 for line in lines if line.startswith("theorem ")]
    lines += [f"#print axioms {law}" for law in LAWS]
    lines += [f"#print axioms {name}" for name in decisions]
    output = kernel(WORK / "admission.lean", lines, args.lean_memory_mb)
    dependencies = {}
    for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]", output, re.DOTALL):
        names = {x.strip() for x in match[2].split(",") if x.strip()}
        if not names <= {"propext", "Classical.choice", "Quot.sound"}:
            raise RuntimeError("unexpected layout proof axioms: " + str(names))
        dependencies[match[1]] = sorted(names)
    for name in re.findall(r"'([^']+)' does not depend on any axioms", output):
        dependencies[name] = []
    if set(dependencies) != set(LAWS + decisions):
        raise RuntimeError("layout axiom audit did not account for every law and decision")
    if hashlib.sha256(BINARY.read_bytes()).hexdigest() != binary_digest:
        raise RuntimeError("Cesta binary changed during layout admission")
    if model_snapshot() != models:
        raise RuntimeError("Lean layout sources or compiled modules changed during admission")
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest for path, digest in modules.items()):
        raise RuntimeError("layout declaration module changed during admission")
    (WORK / "summary.json").write_text(json.dumps({"layouts": report, "laws": LAWS,
        "decisions": decisions, "axioms": dependencies, "cesta_sha256": binary_digest,
        "lean_models": models,
        "declaration_modules": modules,
        "boundary": "Kernel checks certify the normalized layout, declaration metadata conditions and recorded expression lowering. Printed C, compiler fidelity and runtime atomic discipline remain tested boundaries."}, indent=2) + "\n")
    print(f"layout admission: {len(report)} declared layouts kernel-admitted, so the layout laws hold of each; "
          f"the declared frame identities are the layout the Lean side states for CeTTa; "
          f"{sum(e['accessors'] for e in report)} recorded unsigned accessors are exactly the proved lowering; "
          f"{sum('migrates_from' in e for e in report)} version migration admitted; "
          "overlap, wrong accessor/migration masks and five malformed metadata controls are refused; "
          f"{len(decisions)} instance decisions axiom-audited")


if __name__ == "__main__":
    main()
