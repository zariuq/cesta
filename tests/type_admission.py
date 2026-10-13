"""Kernel-check the arithmetic tables of Cesta's data models, and the steps its corpora use,
against the C11 model in Mettapedia."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import metadata_admission as metadata

ROOT = Path(__file__).resolve().parent.parent

INTEGER = {1: ".boolean", 2: ".signed (.standard .char)", 3: ".signed (.standard .char)", 4: ".unsigned (.standard .char)",
           5: ".signed (.standard .short)", 6: ".unsigned (.standard .short)", 7: ".signed (.standard .int)",
           8: ".unsigned (.standard .int)", 9: ".signed (.standard .long)", 10: ".unsigned (.standard .long)",
           11: ".signed (.standard .longLong)", 12: ".unsigned (.standard .longLong)",
           13: ".signed (.extended .int128)", 14: ".unsigned (.extended .int128)"}
REAL = {15: ".float", 16: ".double", 17: ".longDouble", 18: ".float32", 19: ".float64", 20: ".float128",
        21: ".float32x", 22: ".float64x"}

def arithmetic(code):
    """Plain char (2) is signed char on this target: the ABI's char adapter."""
    if code in INTEGER: return f"(.integer ({INTEGER[code]}))"
    if code in REAL: return f"(.real {REAL[code]})"
    if code - 64 in REAL: return f"(.complex {REAL[code - 64]})"
    raise ValueError(code)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--steps", type=Path, default=ROOT / "build/semantic-corpus/arithmetic-steps.txt")
    parser.add_argument("--records", type=Path, default=ROOT / "build/semantic-corpus/natural-records.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/type-admission")
    parser.add_argument("--project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    args = parser.parse_args()
    MODELS = [("lp64", "x86_64-sysv-lp64", "sysvFloating"), ("ilp32", "i386-sysv-ilp32", "sysvFloating"),
              ("llp64", "x86_64-windows-llp64", "windowsFloating")]
    steps = sorted({tuple(map(int, line.split())) for line in args.steps.read_text().splitlines() if line.strip()})
    shapes = json.loads(args.records.read_text())
    code = ["""import Mettapedia.Languages.C11.ArithmeticConversions
import Mettapedia.Languages.C11.ObjectLayout
set_option autoImplicit false
set_option maxRecDepth 65536
set_option maxHeartbeats 0
set_option Elab.async false
namespace CestaTypeAdmission
open Mettapedia.Languages.C11
/-! Cesta's arithmetic tables for three data models, kernel-checked against the
    C11 model. Plain `char` is signed on these targets and enters as `signed char`. -/"""]
    audits, counts, digest = [], {}, hashlib.sha256()
    for model, abi, floating in MODELS:
        tables = subprocess.run([str(args.binary), "abi-tables", "--abi=" + abi], stdout=subprocess.PIPE, check=True).stdout.decode()
        digest.update(tables.encode())
        promotions = [(int(a), int(b)) for a, b in re.findall(r"\(promote (\d+) (\d+)\)", tables)]
        usual = [(int(a), int(b), int(c)) for a, b, c in re.findall(r"\(usual (\d+) (\d+) (\d+)\)", tables)]
        codes = sorted({a for a, b, c in usual})
        counts[model] = (len(promotions), len(usual))
        profile = f"(integerValueProfile .{model})"
        code.append(f"def {model}Promotions : List (IntegerType IntegerExtension × IntegerType IntegerExtension) := [" +
                    ", ".join(f"({INTEGER[a]}, {INTEGER[b]})" for a, b in promotions) + "]")
        code.append(f"theorem {model}Promotions_checked :\n    {model}Promotions.all (fun row => promoteIntegerType {profile} row.1 == row.2) = true := by decide")
        code.append(f"def {model}Usual : List (ArithmeticType IntegerExtension × ArithmeticType IntegerExtension × ArithmeticType IntegerExtension) := [" +
                    ", ".join(f"({arithmetic(a)}, {arithmetic(b)}, {arithmetic(c)})" for a, b, c in usual) + "]")
        code.append(f"theorem {model}Usual_checked :\n    {model}Usual.all (fun row => usualArithmeticType {profile} {floating} row.1 row.2.1 == row.2.2) = true := by decide")
        code.append(f"/-- The arithmetic types this target has. -/\n"
                    f"def {model}Types : List (ArithmeticType IntegerExtension) := [" + ", ".join(arithmetic(c) for c in codes if c != 2) + "]")
        code.append(f"/-- Every pair of this target's arithmetic types has a checked row. -/\n"
                    f"theorem {model}Usual_complete :\n    {model}Types.all (fun a => {model}Types.all fun b =>\n"
                    f"      {model}Usual.any fun row => row.1 == a && row.2.1 == b) = true := by decide")
        audits += [f"{model}Promotions_checked", f"{model}Usual_checked", f"{model}Usual_complete"]
    code.append("/-- Every usual arithmetic conversion the corpora apply is a checked LP64 row. -/\n"
                "def corpusSteps : List (ArithmeticType IntegerExtension × ArithmeticType IntegerExtension) := [" +
                ", ".join(f"({arithmetic(a)}, {arithmetic(b)})" for a, b in steps) + "]")
    code.append("theorem corpusSteps_checked :\n    corpusSteps.all (fun step => lp64Usual.any fun row => row.1 == step.1 && row.2.1 == step.2) = true := by decide")
    # natural record layouts against the C11 ObjectLayout policy
    code.append("""open ObjectLayout in
/-- A layout from an alignment exponent and a size, where the policy admits one. -/
def admittedLayout (exponent bytes : Nat) : Option Layout :=
  if positive : 0 < bytes then
    if aligned : 2 ^ exponent ∣ bytes then some ⟨exponent, bytes, positive, aligned⟩ else none
  else none

open ObjectLayout in
/-- Cesta's record (kind, size, alignment, member offset/exponent/size) agrees with the policy. -/
def recordAgrees : Bool × Nat × Nat × List (Nat × Nat × Nat) → Bool
  | (isUnion, size, alignment, members) =>
    match members.mapM (fun member => admittedLayout member.2.1 member.2.2) with
    | some (first :: rest) =>
        let fields := first :: rest
        let layout := if isUnion then union fields (List.cons_ne_nil first rest) else record fields (List.cons_ne_nil first rest)
        layout.bytes == size && layout.alignment == alignment &&
          (if isUnion then members.all (fun member => member.1 == 0)
           else (place fields 0).1.map Member.offset == members.map (fun member => member.1))
    | _ => false""")
    def exponent(alignment):
        e = alignment.bit_length() - 1
        assert 1 << e == alignment
        return e
    code.append("def corpusRecords : List (Bool × Nat × Nat × List (Nat × Nat × Nat)) := [" + ", ".join(
        f"({'true' if kind == 'union' else 'false'}, {size}, {align}, [" +
        ", ".join(f"({o}, {exponent(a)}, {s})" for o, s, a in members) + "])"
        for kind, size, align, members in shapes) + "]")
    code.append("/-- Every distinct natural record of the corpora is laid out as the policy lays it out. -/\n"
                "theorem corpusRecords_checked : corpusRecords.all recordAgrees = true := by decide")
    code.append("/-- A misplaced member is refused: `struct { char c; int i; }` puts `i` at 4, not 1. -/\n"
                "theorem wrong_offset_refused : recordAgrees (false, 8, 4, [(0, 0, 1), (1, 2, 4)]) = false := by decide")
    audits += ["corpusRecords_checked", "wrong_offset_refused"]
    code.append("/-- A wrong row is refused: `int` with `unsigned int` is not `int`. -/\n"
                "theorem wrong_row_refused :\n    (lp64UsualArithmetic (.integer .int) (.integer .unsignedInt) == .integer .int) = false := by decide")
    code.append("/-- A wrong promotion is refused: `unsigned short` does not promote to `unsigned int` in LP64. -/\n"
                "theorem wrong_promotion_refused :\n    (lp64Promote (.unsigned (.standard .short)) == .unsigned (.standard .int)) = false := by decide")
    code.append("/-- The data models differ: LP64's row for `long` with `unsigned int` is wrong for ILP32. -/\n"
                "theorem data_models_differ :\n    (usualArithmeticType (integerValueProfile .ilp32) sysvFloating (.integer (.signed (.standard .long)))\n"
                "      (.integer .unsignedInt) == .integer (.signed (.standard .long))) = false := by decide")
    audits += ["corpusSteps_checked", "wrong_row_refused", "wrong_promotion_refused", "data_models_differ"]
    code += ["#print axioms " + name for name in audits]
    code.append("end CestaTypeAdmission\n")
    args.output.mkdir(parents=True, exist_ok=True)
    artifact = args.output / "ActualTypeAdmission.lean"
    artifact.write_text("\n\n".join(code))
    command = ["lake", "env", "lean", "-DwarningAsError=true"] + (["-M", str(args.lean_memory_mb)] if args.lean_memory_mb else [])
    result = subprocess.run(command + [str(artifact.resolve())], cwd=args.project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    evidence = result.stdout.decode()
    (args.output / "kernel.log").write_text(evidence)
    if result.returncode:
        raise SystemExit(evidence[-3000:])
    for match in re.finditer(r"depends on axioms:\s*\[(.*?)\]", evidence, re.DOTALL):
        assert {item.strip() for item in match[1].split(",") if item.strip()} <= metadata.ALLOWED_AXIOMS
    reported = set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)", evidence))
    assert reported == {"CestaTypeAdmission." + name for name in audits}, "axiom audit must cover every theorem"
    summary = {"models": counts, "corpus_steps": len(steps), "natural_record_shapes": len(shapes), "theorems": len(audits),
               "tables_sha256": digest.hexdigest()}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    rows = ", ".join(f"{m} {u} rows and {p} promotions" for m, (p, u) in counts.items())
    print(f"type admission: {rows} kernel-checked; {len(steps)} corpus steps covered; "
          f"{len(shapes)} natural record shapes laid out as the C11 layout policy; {len(audits)} theorems")

if __name__ == "__main__":
    main()
