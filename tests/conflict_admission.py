"""Kernel-check the declared direct authority for every production LR conflict."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from lean_contracts import code_without_comments, named_theorems

ROOT = Path(__file__).resolve().parent.parent
MODULE = "Mettapedia.GSLT.Parsing.ExplicitConflictPolicy"


def run(arguments, cwd=ROOT):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
    return result.stdout


def ns(values):
    return "[" + ", ".join(map(str, values)) + "]"


def edges(values):
    return "[" + ", ".join(f"({a}, {b})" for a, b in values) + "]"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    args = parser.parse_args()
    project = args.lean_project.resolve()
    output = ROOT / "build/conflict-admission"
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for grammar in ("C11", "CGNU"):
        data = run([ROOT / "build/conflict-certificate", ROOT / "build" / (grammar + ".cf"),
                    ROOT / "grammar" / (grammar + ".profile")])
        (output / (grammar + ".json")).write_bytes(data)
        selected = json.loads(data)
        if not selected or any(len(row["alternatives"]) < 2 for row in selected):
            raise RuntimeError("missing conflict alternatives")
        rows.extend(selected)
    source = project / (MODULE.replace(".", "/") + ".lean")
    code = code_without_comments(source.read_text())
    if re.search(r'\b(?:sorry|axiom|native_decide|theorem_wanted)\b|_wanted', code):
        raise RuntimeError("unchecked conflict policy theory")
    lean = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb is not None:
        lean += ["-M", str(args.lean_memory_mb)]
    artifact = project / ".lake/build/lib/lean" / (MODULE.replace(".", "/") + ".olean")
    run([*lean, "-o", artifact, source], cwd=project)
    text = ["import " + MODULE, "set_option Elab.async false", "set_option maxRecDepth 10000",
            "set_option maxHeartbeats 0", "open " + MODULE,
            "def actualCells : List (List Nat × List (Nat × Nat) × Nat) := ["]
    text.extend("  (" + ns(row["alternatives"]) + ", " + edges(row["preferences"]) + ", " +
                str(row["selected"]) + ")" + ("," if i + 1 < len(rows) else "]")
                for i, row in enumerate(rows))
    text += ["theorem actualCells_checked : actualCells.all (fun (a, p, s) => select a p == some s) = true := by decide"]
    witness = next(row for row in rows if len(row["alternatives"]) > 2)
    a, p, s = witness["alternatives"], witness["preferences"], witness["selected"]
    necessary = next(edge for edge in p if edge[0] == s and edge[1] != s)
    missing = [edge for edge in p if edge != necessary]
    text += ["theorem missing_direct_edge_refused : select " + ns(a) + " " + edges(missing) +
             " ≠ some " + str(s) + " := by decide",
             "theorem wrong_native_selection_refused : select " + ns(a) + " " + edges(p) +
             " ≠ some " + str((s + 1) % len(a)) + " := by decide"]
    text.extend("#print axioms " + name for name in named_theorems(code))
    text.extend("#print axioms " + name for name in ("actualCells_checked", "missing_direct_edge_refused",
                                                    "wrong_native_selection_refused"))
    admission = output / "Admission.lean"
    admission.write_text("\n".join(text) + "\n")
    log = run([*lean, admission], cwd=project)
    (output / "admission.log").write_bytes(log)
    for axioms in re.findall(rb"depends on axioms: \[([^\]]*)\]", log):
        names = {name.strip() for name in axioms.decode().split(",") if name.strip()}
        if names - {"propext", "Classical.choice", "Quot.sound"}:
            raise RuntimeError("nonstandard logical axiom in conflict admission")
    required = set(named_theorems(code)) | {"actualCells_checked", "missing_direct_edge_refused",
                                           "wrong_native_selection_refused"}
    reported = set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)", log.decode()))
    if required != reported:
        raise RuntimeError("conflict axiom audit missed a theorem")
    print(f"conflict admission: {len(rows)} native conflict/guard rows, {len(named_theorems(code))} structural laws, "
          "two authority/selection mutants refused by the kernel")


if __name__ == "__main__":
    main()
