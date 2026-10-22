"""Connect normalized operation metadata to native invoices and the cursor.

The kernel checks every emitted profile against its operation and an independently
specified fixture mapping/bound. Execution laws concern the declared protocol;
native handlers still owe their invoice, effect and progress refinements.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from export_admission import atom, lean_string  # noqa: E402
from lean_contracts import code_without_comments, named_theorems  # noqa: E402
from oracles.pro_compact_cesta import Sym, Text, parse  # noqa: E402

MODEL = "Mettapedia.GSLT.LanguageDef.OperationCostProtocol"
CONTROLS = MODEL + "Controls"
MODULES = ["Mettapedia.GSLT.LanguageDef.OperationContracts", MODEL, CONTROLS,
           "Mettapedia.Machines.NativeCostLedger", "Mettapedia.Machines.Cursor.Protocol"]
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_snapshot(project):
    out = {}
    for module in MODULES:
        relative = module.replace(".", "/")
        source = project / (relative + ".lean")
        artifact = project / ".lake/build/lib/lean" / (relative + ".olean")
        if not artifact.exists() or artifact.stat().st_mtime_ns < source.stat().st_mtime_ns:
            raise RuntimeError(f"model is not freshly built: {module}; run make test-operation-model")
        out[module] = {"source_sha256": digest(source), "olean_sha256": digest(artifact)}
    return out


def text(value):
    if not isinstance(value, Text):
        raise AssertionError("expected quoted metadata text")
    return value.data.decode()


def change_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError(f"fixture anchor must occur once: {old!r}")
    return source.replace(old, new, 1)


def mapped_fixture(source, step="analysisStep"):
    source = change_once(source,
        '(Charges transition step)\n        (Profiles he_extended petta_extended prime));',
        '(Charges transition step)\n        (Profiles he_extended petta_extended prime)\n'
        f'        (ChargeKinds (transition quantum) (step {step})));')
    source = change_once(source, '(Symbol op_plus) (Dispatch "grounded")',
                         '(Symbol op_plus) (Dispatch "grounded") (ProgressBound 1)')
    for charge, bound in (("transition", 7), ("step", 3)):
        anchor = f'(Context (Blocks "no") (Branches "no") (Progress "bounded") (Charges {charge}))'
        source = change_once(source, anchor, f'(ProgressBound {bound})\n        ' + anchor)
    return source


def expected_profile(operation, mapping, bounds):
    """Expected optional data comes from fixture declarations, not emitted profiles."""
    name = str(operation[1])
    context = operation[7]
    if str(context[0]) != "Context" or str(context[4][0]) != "Charges":
        raise AssertionError("unexpected normalized operation shape")
    progress = text(context[3])
    bound = bounds.get(name, 1 if progress == "single" else None)
    kinds = []
    for label in context[4][1:]:
        native = mapping.get(str(label))
        kinds.append("(" + lean_string(str(label)) + ", " +
                     ("none" if native is None else "some ." + native) + ")")
    replay = text(operation[9][4][1])
    return ("⟨" + lean_string(str(operation[1])) + ", [" + ", ".join(kinds) + "], ." +
            progress + ", " + ("none" if bound is None else "some " + str(bound)) + ", ." + replay + "⟩")


def metadata_mutants(profile):
    """Corrupt emitted metadata while keeping the fixture declaration fixed."""
    changes = []

    def altered(label, edit):
        value = copy.deepcopy(profile)
        edit(value)
        changes.append((label, value))

    altered("name", lambda p: p.__setitem__(1, Sym("undeclared_operation")))
    altered("replay", lambda p: p[4].__setitem__(1,
        Text(b"refused" if text(p[4][1]) == "transparent" else b"transparent")))
    altered("progress", lambda p: p[3].__setitem__(1,
        Text(b"single" if text(p[3][1]) == "unknown" else b"unknown")))
    altered("zero_bound", lambda p: p[3][2].__setitem__(1, Sym("0")))
    altered("extra_charge", lambda p: p[2].append([Sym("undeclared_charge"), Sym("quantum")]))
    if len(profile[2]) > 1:
        altered("missing_charge", lambda p: p[2].pop())
        altered("invalid_native_kind", lambda p: p[2][1].__setitem__(1, Sym("notANativeKind")))
        # This is a valid Kind and thus needs the independent source expectation.
        altered("changed_native_kind", lambda p: p[2][1].__setitem__(1,
            Sym("heapLookup" if p[2][1][1] != Sym("heapLookup") else "quantum")))
    if text(profile[3][1]) == "bounded" and isinstance(profile[3][2][1], Sym):
        altered("changed_valid_bound", lambda p: p[3][2].__setitem__(1,
            Sym(str(int(p[3][2][1]) + 1))))
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "build/operation-protocol")
    args = parser.parse_args()
    binary, project, work = args.binary.resolve(), args.lean_project.resolve(), args.output.resolve()
    if not work.is_relative_to(ROOT / "build"):
        parser.error("test artifacts belong under build")
    work.mkdir(parents=True, exist_ok=True)
    (work / "summary.json").write_text(json.dumps({"pass": False, "status": "running"}) + "\n")
    models = model_snapshot(project)
    fixture = ROOT / "tests/operations/Example.module"
    fingerprints = {str(path): digest(path) for path in (binary, fixture, Path(__file__).resolve())}

    def run(mode, module, accepted=True, diagnostic=None):
        result = subprocess.run([str(binary), "operations", mode, str(module), "Operations"], capture_output=True)
        if (result.returncode == 0) != accepted:
            raise AssertionError(f"operations {mode}: unexpected exit {result.returncode}: " +
                                 result.stderr.decode(errors="replace"))
        if diagnostic is not None and diagnostic not in result.stderr.decode():
            raise AssertionError("missing refusal reason: " + result.stderr.decode())
        return result.stdout

    source = fixture.read_text()
    mapped = mapped_fixture(source)
    explicit_unknown = mapped_fixture(source, '"unknown"')
    partial = change_once(mapped, '(ChargeKinds (transition quantum) (step analysisStep))',
                          '(ChargeKinds (transition quantum))')
    variants = [
        ("base", fixture, source, {}, {}),
        ("mapped", work / "Mapped.module", mapped,
         {"transition": "quantum", "step": "analysisStep"}, {"op_plus": 1, "work_take": 7, "history_record": 3}),
        ("explicit_unknown", work / "Unknown.module", explicit_unknown,
         {"transition": "quantum", "step": None}, {"op_plus": 1, "work_take": 7, "history_record": 3}),
        ("partial", work / "Partial.module", partial,
         {"transition": "quantum"}, {"op_plus": 1, "work_take": 7, "history_record": 3}),
    ]
    baseline_check, baseline_rows = run("check", fixture), run("rows", fixture)
    lines = [f"import {CONTROLS}",
             "open Mettapedia.GSLT.LanguageDef.MacroExportPlan",
             "open Mettapedia.GSLT.LanguageDef.OperationCostProtocol",
             "set_option Elab.async false", "set_option maxRecDepth 4096",
             "namespace CestaOperationProtocolAdmission"]
    decisions, counts, outputs = [], {}, {}

    def theorem(name, proposition):
        lines.append(f"theorem {name} : {proposition} := by decide +kernel")
        decisions.append("CestaOperationProtocolAdmission." + name)

    for label, module, content, mapping, bounds in variants:
        if module != fixture:
            module.write_text(content)
            fingerprints[str(module)] = digest(module)
        normalized, rows = run("check", module), run("rows", module)
        if normalized != baseline_check or rows != baseline_rows:
            raise AssertionError("optional protocol data changed legacy check/rows output")
        records = parse(normalized)
        exported = run("protocols", module)
        if exported != run("protocols", module):
            raise AssertionError("protocol export is not deterministic")
        outputs[label] = exported
        (work / (label + ".protocols")).write_bytes(exported)
        profiles = parse(exported)
        operations = records[1:]
        if not operations or [str(p[1]) for p in profiles] != [str(o[1]) for o in operations]:
            raise AssertionError("missing, duplicate or reordered operation protocol")
        counts[label] = {"operations": len(operations), "mutants": 0}
        for index, (operation, profile) in enumerate(zip(operations, profiles)):
            prefix = label + str(index)
            lines += [f"def {prefix}Op : Atom := {atom(operation)}",
                      f"def {prefix}Profile : Atom := {atom(profile)}",
                      f"def {prefix}Expected : Profile := {expected_profile(operation, mapping, bounds)}"]
            theorem(prefix + "Admitted", f"admitted {prefix}Op {prefix}Profile = true")
            theorem(prefix + "SourceRecorded", f"decodeProfile {prefix}Profile = some {prefix}Expected")
            for mutation, bad in metadata_mutants(profile):
                name = prefix + mutation
                lines.append(f"def {name} : Atom := {atom(bad)}")
                theorem(name + "Refused",
                    f"(admitted {prefix}Op {name} && decide (decodeProfile {name} = some {prefix}Expected)) = false")
                counts[label]["mutants"] += 1
    if outputs["explicit_unknown"] != outputs["partial"]:
        raise AssertionError("an omitted native kind did not remain explicitly unknown")

    source_mutants = [
        ("kind", "(step analysisStep)", "(step notANativeKind)", "native ledger kind"),
        ("label", "(step analysisStep)", "(absent analysisStep)", "declared charge label"),
        ("duplicate", "(step analysisStep)", "(step analysisStep) (step quantum)", "declared twice"),
        ("zero", "(ProgressBound 7)", "(ProgressBound 0)", "positive uint64"),
        ("leading_zero", "(ProgressBound 7)", "(ProgressBound 07)", "positive uint64"),
        ("overflow", "(ProgressBound 7)", "(ProgressBound 18446744073709551616)", "positive uint64"),
        ("single", "(ProgressBound 1)", "(ProgressBound 2)", "only to bounded or single"),
        ("steps", '(Symbol match) (Dispatch "special")',
         '(Symbol match) (Dispatch "special") (ProgressBound 2)', "only to bounded or single"),
    ]
    for label, old, replacement, diagnostic in source_mutants:
        path = work / ("refused_" + label + ".module")
        path.write_text(change_once(mapped, old, replacement))
        run("protocols", path, accepted=False, diagnostic=diagnostic)
        fingerprints[str(path)] = digest(path)

    lines.append("end CestaOperationProtocolAdmission")
    laws = []
    for module in (MODEL, CONTROLS):
        model_source = project / (module.replace(".", "/") + ".lean")
        laws.extend(named_theorems(code_without_comments(model_source.read_text())))
    audited = laws + decisions
    lines += ["#print axioms " + name for name in audited]
    admission = work / "Admission.lean"
    admission.write_text("\n".join(lines) + "\n")
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb is not None:
        command += ["-M", str(args.lean_memory_mb)]
    result = subprocess.run([*command, str(admission)], cwd=project, capture_output=True)
    output = (result.stdout + result.stderr).decode(errors="replace")
    (work / "lean.log").write_text(output)
    if result.returncode or "warning:" in output or "sorryAx" in output:
        raise RuntimeError("protocol admission failed: " + output[-8000:])
    dependencies = {}
    for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]", output, re.S):
        axioms = {item.strip() for item in match[2].split(",") if item.strip()}
        if not axioms <= STANDARD_AXIOMS:
            raise RuntimeError("unexpected axioms: " + repr(axioms))
        dependencies[match[1]] = sorted(axioms)
    for name in re.findall(r"'([^']+)' does not depend on any axioms", output):
        dependencies[name] = []
    if set(dependencies) != set(audited):
        raise RuntimeError("axiom audit omitted or added a theorem")
    if models != model_snapshot(project) or any(digest(Path(p)) != d for p, d in fingerprints.items()):
        raise RuntimeError("protocol verification inputs changed during the run")
    summary = {"pass": True, "binary_sha256": digest(binary), "inputs_stable": True,
               "fixtures": counts, "source_refusals": len(source_mutants),
               "kernel_decisions": len(decisions), "model_law_audits": len(laws),
               "inputs": fingerprints, "models": models, "axioms": dependencies,
               "admission_sha256": digest(admission),
               "boundary": "declarations and model laws; actual native handler refinement remains an obligation"}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("operation protocol: " + str(sum(v["operations"] for v in counts.values())) +
          " source profiles; " + str(sum(v["mutants"] for v in counts.values())) +
          " metadata mutants and " + str(len(source_mutants)) + " native refusals; " +
          str(len(laws)) + " native-ledger/cursor law audits")


if __name__ == "__main__":
    main()
