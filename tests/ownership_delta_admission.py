"""Kernel-check a current ownership module by reusing unchanged admitted functions."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from oracles.pro_compact_cesta import parse
from ownership_admission import (
    admission_projection_sha256,
    module_parts,
    mutations,
    signature,
)

ROOT = Path(__file__).resolve().parent.parent


def run(command, cwd=ROOT, environment=None):
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=environment,
                            capture_output=True, text=True)
    if result.returncode:
        raise AssertionError((command, result.returncode, result.stdout, result.stderr))
    return result


def record(path):
    records = [item for item in parse(Path(path).read_bytes())
               if item and item[0] == "Ownership"]
    assert len(records) == 1
    return records[0]


def module_prefix(stem):
    return "Ownership" + hashlib.sha256(stem.encode()).hexdigest()[:20] + "Parts"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta", type=Path, required=True)
    parser.add_argument("--lean-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--include", type=Path, action="append", default=[])
    parser.add_argument("--define", action="append", default=[])
    parser.add_argument("--base-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    assert args.jobs >= 1
    args.output.mkdir(parents=True, exist_ok=True)

    base_plan = args.base_output / "0.plan"
    base_result = json.loads((args.base_output / "result.json").read_text())[0]
    assert base_result["module_admitted"]

    current_plan = args.output / "current.plan"
    current_c = args.output / "current.c"
    command = [args.cesta, "export-c", args.source.resolve(), "--std=c11",
               "-o", current_c, "--plan", current_plan, "--"]
    command += ["-I" + str(path.resolve()) for path in args.include]
    command += ["-D" + value for value in args.define]
    run(command)

    old_record, new_record = record(base_plan), record(current_plan)
    old_foreign, old_functions = module_parts(old_record)
    new_foreign, new_functions = module_parts(new_record)
    assert old_foreign == new_foreign
    assert len(old_functions) == len(new_functions)
    assert [str(item[1]) for item in old_functions] == [str(item[1]) for item in new_functions]
    assert [signature(item[2]) for item in old_functions] == [signature(item[2]) for item in new_functions]
    changed = [index for index, (old, new) in enumerate(zip(old_functions, new_functions))
               if str(old) != str(new)]
    assert changed, "the current plan is already the admitted plan"
    for index in changed:
        assert len(mutations(old_functions[index])) == len(mutations(new_functions[index]))

    current_output = args.output / "changed"
    environment = dict(os.environ)
    environment["ADMIT_ONLY"] = ",".join(str(new_functions[index][1]) for index in changed)
    command = [sys.executable, "-B", ROOT / "tests/ownership_admission.py",
               "--cesta", args.cesta, "--lean-root", args.lean_root,
               "--source", args.source, "--output", current_output,
               "--admission-stem", "Delta", "--no-cache", "--jobs", str(args.jobs)]
    for path in args.include:
        command += ["--include", path]
    for value in args.define:
        command += ["--define", value]
    run(command, environment=environment)
    assert (current_output / "0.plan").read_bytes() == current_plan.read_bytes()
    delta_result = json.loads((current_output / "result.json").read_text())[0]
    assert delta_result["functions"] == len(changed)
    assert not delta_result["module_admitted"]

    base_prefix = module_prefix("Admission0.lean")
    delta_prefix = module_prefix("Delta0.lean")
    base_parts = args.base_output / base_prefix
    delta_parts = current_output / delta_prefix
    assert (base_parts / "Names.olean").is_file()
    assert (delta_parts / "Names.olean").is_file()
    assert (base_parts / "Names.lean").read_text().replace(base_prefix, delta_prefix) == \
           (delta_parts / "Names.lean").read_text()

    references = []
    imports = []
    acceptances = []
    changed_set = set(changed)
    for index in range(len(new_functions)):
        prefix = delta_prefix if index in changed_set else base_prefix
        parts = delta_parts if index in changed_set else base_parts
        assert (parts / f"Part{index}.olean").is_file()
        imports.append(f"import {prefix}.Part{index}")
        references.append(f"{prefix}.fn{index}")
        if index in changed_set:
            name = f"changed{index}"
            acceptances.append(
                f"    have {name} : admitFunction {base_prefix}.callables "
                f"{delta_prefix}.fn{index} = true := by\n"
                f"      rw [show {base_prefix}.callables = {delta_prefix}.callables from rfl]\n"
                f"      exact {delta_prefix}.accepts{index}")

    foreign_text = ", ".join(
        "⟨" + json.dumps(name) + ", " + contract + ", " + json.dumps(obligation) + "⟩"
        for name, contract, obligation in new_foreign)
    functions_text = ", ".join(references)
    simp = [f"changed{index}" if index in changed_set else f"{base_prefix}.accepts{index}"
            for index in range(len(new_functions))]
    namespace = "OwnershipDelta" + hashlib.sha256(current_plan.read_bytes()).hexdigest()[:20]
    source = args.output / "DeltaModule.lean"
    text = "\n".join(imports + [
        "open Mettapedia.Ownership",
        "set_option maxRecDepth 200000",
        "set_option maxHeartbeats 0",
        f"namespace {namespace}",
        f"def wholeModule : Module := ⟨[{foreign_text}], [{functions_text}]⟩",
        "theorem wholeModule_admitted : admit wholeModule = true := by",
        "  apply admit_of_checks",
        "  · decide",
        "  · decide",
        "  · decide",
        f"  · change [{functions_text}].all (admitFunction {base_prefix}.callables) = true",
        *acceptances,
        "    simp only [List.all_cons, List.all_nil, " + ", ".join(simp) + ", Bool.and_self]",
        f"end {namespace}",
        "",
    ])
    source.write_text(text)
    environment = dict(os.environ)
    environment["LEAN_PATH"] = os.pathsep.join(
        [str(args.base_output.resolve()), str(current_output.resolve()),
         environment.get("LEAN_PATH", "")])
    run(["lake", "env", "lean", "-M", "16384", "--root=" + str(args.output.resolve()),
         "-o", source.with_suffix(".olean"), source], cwd=args.lean_root, environment=environment)

    total_mutants = base_result["mutants"]
    result = {
        "functions": len(new_functions),
        "changed_functions": [str(new_functions[index][1]) for index in changed],
        "reused_functions": len(new_functions) - len(changed),
        "mutants": total_mutants,
        "changed_mutants_rechecked": delta_result["mutants"],
        "foreign_obligations": len(new_foreign),
        "base_plan_sha256": digest(base_plan),
        "current_plan_sha256": digest(current_plan),
        "base_admission_sha256": admission_projection_sha256(old_record),
        "current_admission_sha256": admission_projection_sha256(new_record),
        "module_admitted": True,
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print("ownership delta admission: " + ", ".join(result["changed_functions"]) +
          f" rechecked; {result['reused_functions']} functions reused; current module admitted")


if __name__ == "__main__":
    main()
