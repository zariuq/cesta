"""Kernel checks for actual native-port registries and DefinitionRef data."""

import hashlib
import json
from pathlib import Path
import subprocess

from metadata_admission import lean_string
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build/macro-port-admission"
LEAN = ROOT.parent / "Mettapedia/lean/mettapedia"


def key(a):
    if not isinstance(a, Sym): raise ValueError("expected a definition key")
    return str(a)


def references(a):
    if isinstance(a, list):
        if a and a[0] == Sym("DefinitionRef"):
            if len(a) != 3 or not isinstance(a[1], Text): raise ValueError("invalid DefinitionRef")
            return [(a[1].data.decode("utf-8"), key(a[2]))]
        return [item for child in a for item in references(child)]
    return []


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    module = ROOT / "tests/modules/MacroPorts.module"
    lines = ["import Mettapedia.GSLT.LanguageDef.MacroDefinitionPorts",
             "open Mettapedia.GSLT.LanguageDef.MacroDefinitionPorts",
             "set_option autoImplicit false", "namespace ActualMacroPortAdmission"]
    records = []
    for theory in ("NativeFunctions", "NativeObjects"):
        metadata = subprocess.run([str(ROOT / "build/cesta"), "check-macros", str(module), theory],
                                  cwd=ROOT, capture_output=True, check=True).stdout
        (WORK / (theory + ".metta")).write_bytes(metadata)
        data, = parse(metadata)
        for rule in data[2:]:
            contract = rule[4]
            if contract[1] != Text(b"cesta-macro/2") or len(contract) != 8:
                raise ValueError("port admission requires version 2 data")
            bindings = contract[3][1:]
            definition_keys = [("ordinary", key(port[1])) for port in bindings]
            requested = references(contract[7][1])
            registry = "[" + ", ".join(f"((.{space}, {lean_string(name)}), {i+1})"
                                       for i, (space, name) in enumerate(definition_keys)) + "]"
            terms = "[" + ", ".join(f"(.{space}, {lean_string(name)})" for space, name in requested) + "]"
            lines.append(f"example : admitReferences {registry} {terms} = true := by decide")
            for space, name in requested:
                i = definition_keys.index((space, name))
                lines.append(f"example : acquire {registry} (.{space}, {lean_string(name)}) "
                             f"(.ordinary, \"owned\") = some ⟨(.ordinary, \"owned\"), {i+1}⟩ := by decide")
            lines.append(f"example : admitReferences {registry} [(.ordinary, \"absent_port\")] = false := by decide")
            records.append({"theory": theory, "ports": len(bindings), "references": len(requested),
                            "metadata_sha256": hashlib.sha256(metadata).hexdigest()})
    lines.append("end ActualMacroPortAdmission\n")
    source = WORK / "actual-ports.lean"; source.write_text("\n".join(lines))
    result = subprocess.run(["lake", "env", "lean", str(source)], cwd=LEAN, capture_output=True)
    (WORK / "kernel.log").write_bytes(result.stdout + result.stderr)
    if result.returncode or b"warning:" in result.stdout or b"sorryAx" in result.stdout:
        raise RuntimeError((result.stdout + result.stderr).decode(errors="replace")[-7000:])
    (WORK / "summary.json").write_text(json.dumps({"records": records}, indent=2) + "\n")
    print(f"native port admission: {len(records)} actual registries and negative reference controls", flush=True)


if __name__ == "__main__":
    main()
