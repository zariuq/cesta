"""Kernel admission of the actual module template markers and occurrence counts."""

import hashlib
import json
from pathlib import Path
import subprocess

from metadata_admission import lean_string
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build/macro-admission"
LEAN = ROOT.parent / "Mettapedia/lean/mettapedia"
OWNER = "Mettapedia.GSLT.LanguageDef.MacroTemplateNames"


def symbol(a):
    if not isinstance(a, Sym):
        raise ValueError("expected a symbol")
    return str(a)


def template(a, arguments, locals_, counts, type_counts, type_slots, builtins):
    if isinstance(a, Sym):
        return ".symbol " + lean_string(str(a))
    if isinstance(a, Text):
        return ".text " + lean_string(a.data.decode("utf-8"))
    head = symbol(a[0]) if a and isinstance(a[0], Sym) else ""
    if head == "Argument":
        if len(a) != 2: raise ValueError("argument marker arity")
        index = arguments.index(symbol(a[1])); counts[index] += 1
        return f".argument ⟨{index}, by decide⟩"
    if head == "TypeArgument":
        if len(a) != 2: raise ValueError("type marker arity")
        index = arguments.index(symbol(a[1]))
        if index not in type_slots: raise ValueError("type marker is not a syntax hole")
        type_counts[index] += 1
        return f".typeArgument ⟨{index}, by decide⟩"
    if head == "CompilerRef":
        if len(a) != 2 or symbol(a[1]) not in builtins: raise ValueError("unknown compiler port")
        return ".symbol " + lean_string(builtins[symbol(a[1])])
    if head == "LocalRef":
        if len(a) != 3 or not isinstance(a[1], Text): raise ValueError("local marker fields")
        index = locals_.index((symbol(a[2]), a[1].data.decode("utf-8")))
        return f".bound ⟨{index}, by decide⟩"
    if head == "Capture":
        if len(a) != 3 or not all(isinstance(x, Text) for x in a[1:]): raise ValueError("capture fields")
        return ".capture ." + a[1].data.decode("utf-8") + " " + lean_string(a[2].data.decode("utf-8"))
    return ".list [" + ", ".join("(" + template(x, arguments, locals_, counts,
        type_counts, type_slots, builtins) + ")" for x in a) + "]"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    records = []
    for module, theory in ((ROOT / "tests/modules/MacroControls.module", "Arithmetic"),
                          (ROOT / "tests/modules/MacroTypes.module", "GenericValues"),
                          (ROOT / "macros/Narrowing.module", "Checked"),
                          (ROOT / "macros/Worklists.module", "Growable"),
                          (ROOT / "macros/Sizes.module", "Checked"),
                          (ROOT / "macros/Probes.module", "Switched")):
        metadata = subprocess.run([str(ROOT / "build/cesta"), "check-macros", str(module), theory],
                                  cwd=ROOT, check=True, capture_output=True).stdout
        (WORK / (module.stem + ".metta")).write_bytes(metadata)
        data, = parse(metadata)
        if symbol(data[0]) != "macros" or symbol(data[1]) != "cesta-macro/1":
            raise ValueError("unknown macro metadata version")
        records.extend((rule, hashlib.sha256(metadata).hexdigest()) for rule in data[2:])
    lines = [f"import {OWNER}", "open Mettapedia.GSLT.LanguageDef.MacroTemplateNames",
             "set_option autoImplicit false", "set_option Elab.async false",
             "namespace CestaActualMacroAdmission"]
    counts = []; type_occurrences = []
    for i, (rule, metadata_hash) in enumerate(records):
        if symbol(rule[0]) != "rule" or len(rule) != 5: raise ValueError("bad rule metadata")
        contract = rule[4]
        if symbol(contract[0]) != "Macro" or len(contract) not in (7, 8): raise ValueError("bad macro contract")
        ports = len(contract) == 8
        args = contract[3 + ports][1:]
        names = [symbol(arg[1]) for arg in args]
        locals_ = [(symbol(local[1]), local[2].data.decode("utf-8")) for local in contract[5 + ports][1:]]
        type_slots = [j for j, arg in enumerate(args) if arg[3] == Text(b"syntax")]
        builtins = {symbol(port[1]): port[2].data.decode("utf-8")
                    for port in (contract[3][1:] if ports else []) if port[0] == Sym("CompilerBuiltin")}
        occurrences = [0] * len(args)
        type_counts = [0] * len(args)
        body = template(contract[6 + ports][1], names, locals_, occurrences, type_counts, type_slots, builtins)
        lines.append(f"def rule{i} : Template {len(args)} {len(locals_)} := {body}")
        for j, count in enumerate(occurrences):
            lines.append(f"example : argumentCount rule{i} ⟨{j}, by decide⟩ = {count} := by decide")
            lines.append(f"example : typeArgumentCount rule{i} ⟨{j}, by decide⟩ = {type_counts[j]} := by decide")
            evaluation = args[j][4]
            if evaluation[1] == Text(b"deferred"):
                lines.append(f"example : argumentCount rule{i} ⟨{j}, by decide⟩ = 1 := by decide")
            if evaluation[1] == Text(b"repeat"):
                lines.append(f"example : argumentCount rule{i} ⟨{j}, by decide⟩ = {symbol(evaluation[2])} := by decide")
        for j in range(len(locals_)):
            lines.append(f"example (actual : Fin {len(args)} → Atom) (names : Fin {len(locals_)} → String) "
                         f"(admitted : NamesAdmitted actual names rule{i}) "
                         f"(occurs : names ⟨{j}, by decide⟩ ∈ symbols (instantiate actual names rule{i})) : "
                         f"⟨{j}, by decide⟩ ∈ localSlots rule{i} := "
                         f"emitted_local_reflects actual names rule{i} admitted ⟨{j}, by decide⟩ occurs")
        counts.append(occurrences)
        type_occurrences.append(type_counts)
        # CType fields instantiate type holes by the same structural rule;
        # executable argument markers are not allowed in these fields.
        types = [arg[2] for arg in args] + [contract[4 + ports][1]]
        for k, type_ in enumerate(types):
            type_expression_counts = [0] * len(args); type_marker_counts = [0] * len(args)
            typed = template(type_, names, [], type_expression_counts, type_marker_counts, type_slots, {})
            if any(type_expression_counts): raise ValueError("expression marker in CType")
            lines.append(f"def type{i}_{k} : Template {len(args)} 0 := {typed}")
            for j, count in enumerate(type_marker_counts):
                lines.append(f"example : typeArgumentCount type{i}_{k} ⟨{j}, by decide⟩ = {count} := by decide")
    lines.extend(["end CestaActualMacroAdmission", ""])
    source = WORK / "actual-rules.lean"; source.write_text("\n".join(lines))
    result = subprocess.run(["lake", "env", "lean", str(source)], cwd=LEAN, capture_output=True)
    (WORK / "kernel.log").write_bytes(result.stdout + result.stderr)
    if result.returncode or b"warning:" in result.stdout or b"sorryAx" in result.stdout:
        raise RuntimeError((result.stdout + result.stderr).decode(errors="replace")[-8000:])
    summary = {"templates": len(counts), "argument_occurrences": counts, "type_occurrences": type_occurrences,
               "metadata_sha256": sorted(set(metadata_hash for _, metadata_hash in records)),
               "proof_scope": "slot bounds, expression/type counts, symbol origin under independent freshness"}
    (WORK / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"macro admission: {len(counts)} actual module templates; kernel-checked slots and counts", flush=True)


if __name__ == "__main__":
    main()
