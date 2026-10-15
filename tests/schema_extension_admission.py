"""Kernel-check structural extension of the actual C11 and GNU shape tables.

The lexical provider is universally quantified and identical on both sides.
This check does not certify changed lexer priorities, source interpretations,
binding programs, native code or migration of sealed document versions.
"""

import argparse
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path

import metadata_admission as metadata
from presentation_admission import prepared_entries

ROOT = Path(__file__).resolve().parent.parent


def lowered_rows(schema, prefix):
    categories = {row["name"]: row["sort"] for row in schema["categories"]}
    rows = []
    for i, row in enumerate(schema["productions"]):
        if row["projection"]["kind"] == "constructor":
            arguments = [categories[slot["category"]] for slot in row["rhs"] if slot["kind"] == "argument"]
            declaration = "⟨" + metadata.lean_string(row["projection"]["head"]) + ", [" + ", ".join(
                map(metadata.lean_string, arguments)) + "]⟩"
            rows.append(f"⟨{prefix}Row{i}, " + metadata.lean_string(categories[row["resultCategory"]]) + ", " + declaration + "⟩")
    return "[" + ", ".join(rows) + "]"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "build/schema-extension-admission")
    args = parser.parse_args()
    binary, output, project = args.binary.resolve(), args.output.resolve(), args.lean_project.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("admission artifacts belong under build")
    output.mkdir(parents=True, exist_ok=True)
    inputs = [binary, ROOT / "build/C11.cf", ROOT / "build/CGNU.cf",
              ROOT / "grammar/C11.profile", ROOT / "grammar/CGNU.profile"]
    fingerprints = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    exports = {}
    for key, grammar, profile in (("c11", "C11.cf", "C11.profile"), ("gnu", "CGNU.cf", "CGNU.profile")):
        result = subprocess.run([str(binary), "export-metadata", str(ROOT / "build" / grammar),
                                 str(ROOT / "grammar" / profile)], capture_output=True, check=True)
        (output / (key + ".json")).write_bytes(result.stdout)
        exports[key] = json.loads(result.stdout)
        metadata.validate_json(exports[key])
    code = ["""import Mettapedia.GSLT.Parsing.SortedSchemaExtension
set_option autoImplicit false
set_option Elab.async false
set_option maxRecDepth 20000
set_option maxHeartbeats 5000000
namespace CestaSchemaExtensionAdmission
open Mettapedia.GSLT.Parsing
open SortedCanonicalSExprCodec SortedCodecMetadataAdmission PreparedSortedCodecSchema
def actualNotation : Notation where
  safeSymbol := fun _ => false
"""]
    audits = []
    for key, schema in exports.items():
        code.append(metadata.metadata_definition(key, schema))
        code.append(f"theorem {key}_metadata_admitted : SortedCodecMetadataAdmission.check {key} actualNotation = true := by decide +kernel")
        code.append(f"def {key}Lowered : List LoweredConstructor := {lowered_rows(schema, key)}")
        code.append(f"theorem {key}_lowered_checked : constructorRows {key} = {key}Lowered := by rfl")
        code.append(f"def {key}Prepared : List Entry := {prepared_entries(schema)}")
        code.append(f"theorem {key}_prepared_checked : prepare {key} = {key}Prepared := by\n"
                    f"  change prepareUsing {key} (constructorRows {key}) = {key}Prepared\n"
                    f"  rw [{key}_lowered_checked]\n  rfl")
        audits += [key + suffix for suffix in ("_metadata_admitted", "_lowered_checked", "_prepared_checked")]
    code.append("theorem actual_extension_checked : SortedSchemaExtension.check actualNotation c11Prepared gnuPrepared = true := by decide +kernel")
    code.append("""theorem actual_c11_wire_preserved
    (lexical : String → String → Bool) (sort : String)
    (wire : Mettapedia.Languages.MeTTa.OSLFCore.Atom)
    (tree : CanonicalGrammar.CanonicalTerm)
    (decoded : decode? (preparedSchema c11Prepared lexical) actualNotation sort wire = some tree) :
    decode? (preparedSchema gnuPrepared lexical) actualNotation sort wire = some tree :=
  SortedSchemaExtension.old_wire_preserved actualNotation c11Prepared gnuPrepared
    actual_extension_checked lexical sort wire tree decoded
""")
    audits += ["actual_extension_checked", "actual_c11_wire_preserved"]
    code.append("""theorem actual_template_certificate_transports
    (lexical : String → String → Bool) (slots : List String) (sort : String)
    (body : CheckedTreePresentation.Template)
    (typed : SortedTreePresentationTemplates.Typed (preparedSchema c11Prepared lexical) actualNotation slots sort body) :
    SortedTreePresentationTemplates.Typed (preparedSchema gnuPrepared lexical) actualNotation slots sort body :=
  SortedSchemaExtension.template_transport actualNotation c11Prepared gnuPrepared
    actual_extension_checked lexical slots sort body typed
""")
    audits.append("actual_template_certificate_transports")
    mutant = copy.deepcopy(exports["gnu"])
    row = next(row for row in mutant["productions"] if row["projection"].get("head") == "FunctionDefinition")
    indexes = [i for i, slot in enumerate(row["rhs"]) if slot["kind"] == "argument"]
    row["rhs"][indexes[0]], row["rhs"][indexes[1]] = row["rhs"][indexes[1]], row["rhs"][indexes[0]]
    code.append("def wrongOrder : List Entry := " + prepared_entries(mutant))
    code.append("theorem actual_argument_order_mutant_refused : SortedSchemaExtension.check actualNotation c11Prepared wrongOrder = false := by decide +kernel")
    code.append("def duplicateSort : List Entry := gnuPrepared ++ [⟨" + metadata.lean_string(exports["gnu"]["sorts"][0]["name"]) + ", .token .literal⟩]")
    code.append("theorem actual_duplicate_sort_mutant_refused : SortedSchemaExtension.check actualNotation c11Prepared duplicateSort = false := by decide +kernel")
    audits += ["actual_argument_order_mutant_refused", "actual_duplicate_sort_mutant_refused"]
    code += ["#print axioms " + name for name in audits]
    code.append("end CestaSchemaExtensionAdmission\n")
    source = output / "Admission.lean"
    source.write_text("\n\n".join(code))
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb is not None:
        command += ["-M", str(args.lean_memory_mb)]
    result = subprocess.run([*command, str(source)], cwd=project, capture_output=True)
    (output / "lean.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("schema extension admission failed: " + result.stdout.decode(errors="replace")[-5000:])
    dependencies = {}
    for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]", result.stdout.decode(), re.S):
        used = {value.strip() for value in match[2].split(",") if value.strip()}
        if not used <= metadata.ALLOWED_AXIOMS:
            raise RuntimeError("unexpected axioms: " + repr(used))
        dependencies[match[1]] = sorted(used)
    for name in re.findall(r"'([^']+)' does not depend on any axioms", result.stdout.decode()):
        dependencies[name] = []
    if set(dependencies) != {"CestaSchemaExtensionAdmission." + name for name in audits}:
        raise RuntimeError("axiom audit omitted a theorem")
    if fingerprints != {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}:
        raise RuntimeError("schema admission inputs changed during verification")
    summary = {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "constructors": {key: sum(row["projection"]["kind"] == "constructor" for row in data["productions"])
                                for key, data in exports.items()},
               "sorts": {key: len(data["sorts"]) for key, data in exports.items()},
               "kernel_theorems": len(audits), "negative_controls": 2,
               "inputs_stable": True, "inputs": fingerprints,
               "axioms": dependencies, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"schema extension: actual C11/GNU tables {summary['constructors']}; "
          f"{len(audits)} kernel theorems; two actual-table mutants refused")


if __name__ == "__main__":
    main()
