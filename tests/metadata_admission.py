#!/usr/bin/env python3
"""Kernel-check actual native finite metadata through Mettapedia's admission gate.

The JSON adapter preserves all exported ordered rows. It neither reconstructs
lost module origins nor proves native parser/lexer/exporter correspondence.
Generated Lean files and run evidence stay beneath the selected output folder.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "cesta.sorted-metadata.v1"
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def require(condition, message):
    if not condition: raise ValueError(message)


def fields(value, expected, where):
    require(type(value) is dict and set(value) == set(expected),
            f"{where}: expected exactly {expected}")


def string(value, where):
    require(type(value) is str, f"{where}: expected a string")
    require(all(not 0xd800 <= ord(c) <= 0xdfff for c in value), f"{where}: unpaired surrogate")
    return value


def maybe_string(value, where):
    if value is not None: string(value, where)


def validate_json(data):
    """Check wire types only; declaration/reference validity is left to Lean."""
    fields(data, ["format", "profile", "sorts", "categories", "productions"], "metadata")
    require(data["format"] == FORMAT, "unsupported finite metadata format")
    string(data["profile"], "profile")
    for table in ("sorts", "categories", "productions"):
        require(type(data[table]) is list, f"{table}: expected an ordered array")
    for i,row in enumerate(data["sorts"]):
        fields(row,["name", "shape"],f"sorts[{i}]")
        string(row["name"], "sort name")
        shape = row["shape"]
        require(type(shape) is dict, "sort shape must be an object")
        kind = shape.get("kind")
        if kind == "constructors": fields(shape,["kind"],"constructor sort")
        elif kind == "token":
            fields(shape,["kind", "representation", "head"],"token sort")
            require(shape["representation"] in ("literal", "identifier"), "unsupported token representation")
            string(shape["head"], "token head")
        elif kind == "list":
            fields(shape,["kind", "element", "key"],"list sort")
            string(shape["element"], "list element sort")
            string(shape["key"], "list projection key")
        else: raise ValueError("unsupported sort shape")
    for i,row in enumerate(data["categories"]):
        fields(row,["name", "sort", "kind"],f"categories[{i}]")
        string(row["name"], "category name")
        maybe_string(row["sort"], "category logical sort")
        require(row["kind"] in ("rule", "token", "erased"), "unsupported source category kind")
    for i,row in enumerate(data["productions"]):
        fields(row,["occurrence", "origin", "resultCategory", "rhs", "projection"],f"productions[{i}]")
        require(type(row["occurrence"]) is int and row["occurrence"] >= 0, "occurrence must be a natural number")
        maybe_string(row["origin"], "production origin")
        string(row["resultCategory"], "production result category")
        require(type(row["rhs"]) is list, "production RHS must be an ordered array")
        for slot in row["rhs"]:
            require(type(slot) is dict, "RHS slot must be an object")
            if slot.get("kind") in ("argument", "erased"):
                fields(slot,["kind", "category"],"category slot")
                string(slot["category"], "slot category")
            elif slot.get("kind") == "fixed":
                fields(slot,["kind", "terminal", "surface"],"fixed slot")
                string(slot["terminal"], "terminal identity")
                maybe_string(slot["surface"], "terminal surface")
            else: raise ValueError("unsupported RHS slot kind")
        projection = row["projection"]
        require(type(projection) is dict, "projection must be an object")
        if projection.get("kind") == "constructor":
            fields(projection,["kind", "head"],"constructor projection")
            string(projection["head"], "constructor head")
        elif projection.get("kind") == "listCons":
            fields(projection,["kind", "left"],"list cons projection")
            require(type(projection["left"]) is bool, "list direction must be a Boolean")
        else:
            fields(projection,["kind"],"projection")
            require(projection["kind"] in ("transparent", "listEmpty", "listSingleton", "erased"),
                    "unsupported projection kind")
    return data


def lean_string(value):
    # JSON's backspace/form-feed and surrogate-pair escapes are not Lean string
    # escapes. Preserve scalar values through four-digit control escapes and
    # actual UTF-8 characters instead of reusing serialized JSON text.
    out = ['"']
    for c in value:
        if c == '"': out.append('\\"')
        elif c == "\\": out.append("\\\\")
        elif c == "\n": out.append("\\n")
        elif c == "\r": out.append("\\r")
        elif c == "\t": out.append("\\t")
        elif ord(c) < 32 or ord(c) == 127: out.append("\\u"+f"{ord(c):04x}")
        else: out.append(c)
    return "".join(out)+'"'


def lean_option(value): return "none" if value is None else "(some "+lean_string(value)+")"


def lean_shape(shape):
    if shape["kind"] == "token":
        return ".token ."+shape["representation"]+" "+lean_string(shape["head"])
    if shape["kind"] == "list": return ".list "+lean_string(shape["element"])+" "+lean_string(shape["key"])
    return ".constructors"


def lean_slot(slot):
    if slot["kind"] == "fixed": return ".fixed "+lean_string(slot["terminal"])+" "+lean_option(slot["surface"])
    return "."+slot["kind"]+" "+lean_string(slot["category"])


def lean_projection(projection):
    kind = projection["kind"]
    if kind == "constructor": return ".constructor "+lean_string(projection["head"])
    if kind == "listCons": return ".listCons "+str(projection["left"]).lower()
    return "."+kind


def lean_row(row):
    return "⟨"+", ".join([str(row["occurrence"]), lean_option(row["origin"]), lean_string(row["resultCategory"]),
                          "["+", ".join(lean_slot(slot) for slot in row["rhs"])+"]",
                          lean_projection(row["projection"])])+"⟩"


def metadata_definition(name, data):
    lines = []
    for i,row in enumerate(data["productions"]): lines.append(f"def {name}Row{i} : ProductionRow := "+lean_row(row))
    lines.extend([f"def {name} : Metadata where", "  sorts := ["])
    lines.append("    "+",\n    ".join("⟨"+lean_string(row["name"])+", "+lean_shape(row["shape"])+"⟩" for row in data["sorts"])+"]")
    lines.append("  categories := [")
    lines.append("    "+",\n    ".join("⟨"+lean_string(row["name"])+", "+lean_option(row["sort"])+", ."+row["kind"]+"⟩" for row in data["categories"])+"]")
    lines.append("  productions := ["+", ".join(f"{name}Row{i}" for i in range(len(data["productions"])))+"]")
    return "\n".join(lines)


LEAN_PREAMBLE = '''import Mettapedia.GSLT.Parsing.SortedCodecMetadataAdmission

/-! Actual exported finite rows. Source derivability, byte escaping, lexical
priority, native parsing/export fidelity and module origins are separate. -/

set_option autoImplicit false
set_option maxRecDepth 65536
set_option maxHeartbeats 0

namespace CestaNativeMetadataAdmission
open Mettapedia.GSLT.Parsing
open SortedCodecMetadataAdmission
open SortedCanonicalSExprCodec

def asciiHead (c : Char) : Bool :=
  c == '_' || (97 ≤ c.toNat && c.toNat ≤ 122) || (65 ≤ c.toNat && c.toNat ≤ 90)

def asciiTail (c : Char) : Bool := asciiHead c || (48 ≤ c.toNat && c.toNat ≤ 57)

def actualNotation : Notation where
  safeSymbol spelling := match spelling.toList with
    | [] => false
    | first :: rest => asciiHead first && rest.all asciiTail && spelling != "True" && spelling != "False" && spelling != "PI" && spelling != "EXP"
'''


def generated_lean(exports):
    code = [LEAN_PREAMBLE]
    audits = []
    for profile,data in exports.items():
        name = "actual"+profile
        code.append(metadata_definition(name,data))
        theorem = name+"_checked"
        code.append(f"theorem {theorem} : check {name} actualNotation = true := by decide +kernel")
        code.append(f"theorem {name}_valid : Valid {name} actualNotation :=\n  (check_iff {name} actualNotation).mp {theorem}")
        code.append(f"theorem {name}_unique_lowered_occurrences :\n  ((constructorRows {name}).map (fun row => row.source.occurrence)).Nodup :=\n  constructorRows_unique_occurrences {name} actualNotation {name}_valid")
        code.append(f"theorem {name}_ordered_constructor_keys :\n  (constructorRows {name}).map LoweredConstructor.key =\n    {name}.productions.filterMap (constructorKey? {name}) :=\n  constructorRows_keys {name} actualNotation {name}_valid")
        code.append(f"theorem {name}_origins_unavailable :\n  {name}.productions.all (fun row => row.origin.isNone) = true := by decide +kernel")
        audits.extend([theorem,name+"_valid",name+"_unique_lowered_occurrences",name+"_ordered_constructor_keys",name+"_origins_unavailable"])
        positives,positive_names = positive_controls(name,data)
        code.extend(positives)
        audits.extend(positive_names)
    controls,control_names = negative_controls(exports)
    code.extend(controls)
    audits.extend(control_names)
    for name in ["check_iff", *audits]: code.append("#print axioms "+name)
    code.append("end CestaNativeMetadataAdmission\n")
    return "\n\n".join(code),audits


def find_row(data, predicate, what):
    for i,row in enumerate(data["productions"]):
        if predicate(row): return i,row
    raise ValueError("actual export has no "+what)


def positive_controls(name,data):
    code,names = [],[]
    categories = {row["name"]: row for row in data["categories"]}
    for left in (True,False):
        i,row = find_row(data,lambda r: r["projection"] == {"kind": "listCons", "left": left},
                         "left/right list-spine production")
        arguments = [slot["category"] for slot in row["rhs"] if slot["kind"] == "argument"]
        theorem = name+("_left" if left else "_right")+"_ordered_spine"
        code.append(f"theorem {theorem} :\n  argumentNames {name}Row{i}.rhs = ["+", ".join(lean_string(a) for a in arguments)+f"] ∧\n  listSpineCheck {name}Row{i} = true := by decide +kernel")
        names.append(theorem)
    i,row = find_row(data,lambda r: r["projection"].get("head") == "FunctionDefinition" and
                     any(slot["kind"] == "erased" for slot in r["rhs"]),"profile-erased function definition")
    arguments = [slot["category"] for slot in row["rhs"] if slot["kind"] == "argument"]
    sorts = [categories[a]["sort"] for a in arguments]
    require(all(type(s) is str for s in sorts), "function definition has missing exported logical argument sorts")
    result_sort = categories[row["resultCategory"]]["sort"]
    theorem = name+"_function_lowering_preserves_order"
    code.append(f"theorem {theorem} :\n  constructorRow? {name} {name}Row{i} =\n    some ⟨{name}Row{i}, "+lean_string(result_sort)+", ⟨\"FunctionDefinition\", ["+", ".join(lean_string(s) for s in sorts)+"]⟩⟩ := by decide +kernel")
    names.append(theorem)
    theorem = name+"_profile_slots_require_separate_surface_correspondence"
    code.append(f"theorem {theorem} :\n  surfaceProduction? {name} (fun occurrence => toString occurrence) {name}Row{i} = none := by decide +kernel")
    names.append(theorem)
    token = categories["CIdent"]
    representation = next(s["shape"] for s in data["sorts"] if s["name"] == token["sort"])
    require(representation == {"kind": "token", "representation": "identifier", "head": "CIdent"},
            "actual C identifier projection changed")
    theorem = name+"_identifier_shape_uses_declared_sort"
    code.append(f"theorem {theorem} (acceptsToken : String → String → Bool) :\n  (schema {name} acceptsToken).shape? "+lean_string(token["sort"])+" = some (.token .identifier) := by rfl")
    names.append(theorem)
    # Different physical precedence names remain present despite a shared
    # logical sort supplied by actual transparent declarations.
    require("Expr14" in categories and "Expr15" in categories, "precedence-category controls missing")
    theorem = name+"_physical_precedence_categories_retained"
    code.append(f"theorem {theorem} :\n  category? {name} \"Expr14\" = some ⟨\"Expr14\", "+lean_option(categories["Expr14"]["sort"])+", .rule⟩ ∧\n"+
                f"  category? {name} \"Expr15\" = some ⟨\"Expr15\", "+lean_option(categories["Expr15"]["sort"])+", .rule⟩ := by decide +kernel")
    names.append(theorem)
    enumerator_sort = categories["[Enumerator]"]["sort"]
    theorem = name+"_structural_list_admission_keeps_source_cardinality_external"
    code.append(f"theorem {theorem} (acceptsToken : String → String → Bool) :\n"+
                f"  {name}.productions.any (fun row => row.resultCategory == \"[Enumerator]\" &&\n"+
                "    row.projection == Projection.listEmpty) = false ∧\n"+
                f"  decode? (schema {name} acceptsToken) actualNotation "+lean_string(enumerator_sort)+
                " (.expression [.symbol \"c:List\"]) = some (.seq []) := by\n"+
                "  constructor\n  · decide +kernel\n  · rfl")
    names.append(theorem)
    return code,names


def negative_controls(exports):
    # Controls mutate real exported rows. They are not pre-rejected by the
    # Python wire-type adapter: the generated Lean checker must refuse them.
    profile = "C11" if "C11" in exports else next(iter(exports))
    name,data = "actual"+profile,exports[profile]
    code,names = [],[]
    def production_mutation(label,index,modified,extra_categories=None):
        validate_json({**data,"productions": [modified]})
        constant = "corrupt"+label
        code.append(f"def {constant}Row : ProductionRow := "+lean_row(modified))
        definition = f"def {constant} : Metadata := {{ {name} with\n  productions := {name}.productions.map (fun row =>\n    if row.occurrence == {data['productions'][index]['occurrence']} then {constant}Row else row)"
        if extra_categories:
            definition += f"\n  categories := {name}.categories ++ ["+", ".join(
                "⟨"+lean_string(row["name"])+", "+lean_option(row["sort"])+", ."+row["kind"]+"⟩" for row in extra_categories)+"]"
        code.append(definition+" }")
        theorem = constant+"_refused"
        code.append(f"theorem {theorem} : check {constant} actualNotation = false := by decide +kernel")
        names.append(theorem)
    i,row = find_row(data,lambda r: any(s["kind"] == "argument" for s in r["rhs"]),"argument reference")
    modified = copy.deepcopy(row)
    next(s for s in modified["rhs"] if s["kind"] == "argument")["category"] = "__CestaMissingCategory"
    production_mutation("MissingArgument",i,modified)
    constant = "corruptDuplicateOccurrence"
    code.append(f"def {constant} : Metadata := {{ {name} with productions := {name}.productions ++ [{name}Row0] }}")
    theorem = constant+"_refused"
    code.append(f"theorem {theorem} : check {constant} actualNotation = false := by decide +kernel")
    names.append(theorem)
    i,row = find_row(data,lambda r: r["projection"]["kind"] == "listCons", "list-spine production")
    modified = copy.deepcopy(row)
    visible = [s for s in modified["rhs"] if s["kind"] == "argument"]
    visible[0 if row["projection"]["left"] else 1]["category"] = "__CestaWrongPhysicalSpine"
    logical = next(c["sort"] for c in data["categories"] if c["name"] == row["resultCategory"])
    production_mutation("WrongPhysicalSpine",i,modified,[{"name": "__CestaWrongPhysicalSpine", "sort": logical, "kind": "rule"}])
    i,row = find_row(data,lambda r: any(s["kind"] == "erased" for s in r["rhs"]), "erased profile slot")
    modified = copy.deepcopy(row)
    next(s for s in modified["rhs"] if s["kind"] == "erased")["kind"] = "argument"
    production_mutation("ErasedAsVisibleArgument",i,modified)
    i,row = find_row(data,lambda r: r["projection"]["kind"] == "constructor", "constructor projection")
    modified = copy.deepcopy(row)
    modified["projection"]["head"] = "List"
    production_mutation("ReservedConstructor",i,modified)
    token_sort = next(s for s in data["sorts"] if s["shape"].get("head") == "CIdent")
    constant = "corruptTokenHead"
    code.append(f"def {constant} : Metadata := {{ {name} with sorts := {name}.sorts.map (fun row =>\n  if row.name == "+lean_string(token_sort["name"])+" then { row with shape := .token .identifier \"OtherIdentifierToken\" } else row) }")
    theorem = constant+"_refused"
    code.append(f"theorem {theorem} : check {constant} actualNotation = false := by decide +kernel")
    names.append(theorem)
    list_sort = next(s for s in data["sorts"] if s["shape"]["kind"] == "list")
    constant = "corruptMissingListElement"
    code.append(f"def {constant} : Metadata := {{ {name} with sorts := {name}.sorts.map (fun row =>\n  if row.name == "+lean_string(list_sort["name"])+" then { row with shape := .list \"__CestaMissingElementSort\" "+lean_string(list_sort["shape"]["key"])+" } else row) }")
    theorem = constant+"_refused"
    code.append(f"theorem {theorem} : check {constant} actualNotation = false := by decide +kernel")
    names.append(theorem)
    # Closed carrier checks exercise escaping without implying native byte
    # escaping correspondence or accepting every scalar in the C lexer.
    payload = '\x00\b\f\n\r\t\x1f\x7f"\\λ😀'
    code.append("theorem generated_string_carrier_preserves_scalars :\n  "+lean_string(payload)+
                " = String.ofList ["+", ".join(f"Char.ofNat {ord(c)}" for c in payload)+"] := by decide +kernel")
    names.append("generated_string_carrier_preserves_scalars")
    code.append('theorem ascii_symbol_policy_controls :\n  ["x", "_", "x2"].all actualNotation.safeSymbol = true ∧\n  ["", "2x", "True", "False", "$x", "λ"].all (fun s => !actualNotation.safeSymbol s) = true := by decide +kernel')
    names.append("ascii_symbol_policy_controls")
    return code,names


def capture(command, cwd, log=None):
    result = subprocess.run([str(x) for x in command],cwd=cwd,capture_output=True,text=True)
    if log is not None: log.write_text(result.stdout+result.stderr)
    require(result.returncode == 0, f"command failed ({result.returncode}): {' '.join(map(str,command))}\n{result.stdout}{result.stderr}")
    return result.stdout


def export_profile(binary, build_dir, profile_dir, profile, output):
    grammar = build_dir/(profile+".cf")
    profile_path = profile_dir/(profile+".profile")
    base_stdout = capture([binary,"check-grammar",grammar],ROOT)
    counts = re.fullmatch(r"grammar: (\d+) productions, (\d+) symbols, (\d+) lexical rules\n",base_stdout)
    require(counts is not None, "unrecognized check-grammar count output")
    base_counts = dict(zip(("productions", "symbols", "tokens"),map(int,counts.groups())))
    raw_base = capture([binary,"export-metadata",grammar],ROOT)
    raw_profile = capture([binary,"export-metadata",grammar,profile_path],ROOT)
    base = validate_json(json.loads(raw_base))
    data = validate_json(json.loads(raw_profile))
    require(base["profile"] == "" and data["profile"] == profile,
            "exported profile identity differs from the selected native profile")
    (output/(profile+"-base.json")).write_text(raw_base)
    (output/(profile+".json")).write_text(raw_profile)
    require(len(base["productions"]) == base_counts["productions"], "base export omitted/added production occurrences")
    fixed = {slot["terminal"] for row in base["productions"] for slot in row["rhs"] if slot["kind"] == "fixed"}
    require(len(base["categories"])+len(fixed) == base_counts["symbols"], "base export category/fixed counts do not account for all symbols")
    require(sum(row["kind"] == "token" for row in base["categories"]) == base_counts["tokens"], "base export token count differs")
    non_erased = [row for row in data["categories"] if row["kind"] != "erased"]
    require(non_erased == base["categories"], "profile export changed base source-category names/order/sorts")
    require(len(data["productions"]) >= len(base["productions"]), "profile export lost source occurrences")
    for original,profiled in zip(base["productions"],data["productions"]):
        for field in ("occurrence", "origin", "resultCategory", "projection"):
            require(original[field] == profiled[field], f"profile export changed source {field}")
        def physical(slots):
            return [{k:v for k,v in slot.items() if k != "surface"} for slot in slots if slot["kind"] != "erased"]
        require(physical(original["rhs"]) == physical(profiled["rhs"]), "profile export changed physical argument/fixed-terminal order")
    erased = {row["name"] for row in data["categories"] if row["kind"] == "erased"}
    extra = data["productions"][len(base["productions"]):]
    require(all(row["resultCategory"] in erased and row["projection"]["kind"] == "erased" for row in extra),
            "profile extension added an undeclared visible occurrence")
    require(len(extra) == len(erased), "profile event row/category counts differ")
    require([row["occurrence"] for row in data["productions"]] == list(range(len(data["productions"]))),
            "native occurrence export is not complete and ordered")
    info = {"check_grammar_before_profile": base_counts,
            "sorts": len(data["sorts"]), "categories": len(data["categories"]), "productions": len(data["productions"]),
            "scope_erased_categories": len(erased), "scope_erased_extra_rows": len(extra),
            "projections": dict(Counter(row["projection"]["kind"] for row in data["productions"])),
            "metadata_sha256": hashlib.sha256(raw_profile.encode()).hexdigest(),
            "grammar_sha256": hashlib.sha256(grammar.read_bytes()).hexdigest(),
            "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest()}
    return data,info


def audit_axioms(text, theorem_names):
    entries = {}
    for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]",text,re.DOTALL):
        axioms = set(x.strip() for x in match[2].split(",") if x.strip())
        require(axioms <= ALLOWED_AXIOMS, f"unexpected axioms in {match[1]}: {axioms-ALLOWED_AXIOMS}")
        entries[match[1]] = sorted(axioms)
    for match in re.finditer(r"'([^']+)' does not depend on any axioms",text): entries[match[1]] = []
    for name in theorem_names:
        require(any(key.endswith("."+name) or key == name for key in entries), f"missing axiom audit for {name}")
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cesta",type=Path,default=ROOT/"build"/"cesta")
    parser.add_argument("--build-dir",type=Path,default=ROOT/"build")
    parser.add_argument("--profile-dir",type=Path,default=ROOT/"grammar")
    parser.add_argument("--lean-project",type=Path,default=ROOT.parent/"Mettapedia"/"lean"/"mettapedia")
    parser.add_argument("--output",type=Path,default=ROOT/"build"/"audit-metadata")
    parser.add_argument("--lean-memory-mb",type=int,help="optional Lean process memory budget")
    parser.add_argument("--profiles",nargs="+",choices=("C11", "CGNU"),default=["C11", "CGNU"])
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        output.mkdir(parents=True,exist_ok=True)
        exports, evidence = {}, {}
        for profile in args.profiles:
            exports[profile],evidence[profile] = export_profile(args.cesta.resolve(),args.build_dir.resolve(),args.profile_dir.resolve(),profile,output)
        source,audits = generated_lean(exports)
        lean_file = output/"ActualNativeMetadataAdmission.lean"
        lean_file.write_text(source)
        start = time.monotonic()
        command = ["lake", "env", "lean"]
        if args.lean_memory_mb is not None:
            require(args.lean_memory_mb > 0,"Lean memory budget must be positive")
            command.extend(["-M",str(args.lean_memory_mb)])
        command.extend(["-DwarningAsError=true",lean_file])
        log = capture(command,args.lean_project,output/"lean-check.log")
        evidence["lean_seconds"] = time.monotonic()-start
        evidence["axioms"] = audit_axioms(log,audits)
        evidence["generated_source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
        evidence["native_binary_sha256"] = hashlib.sha256(args.cesta.resolve().read_bytes()).hexdigest()
        evidence["lean_toolchain"] = (args.lean_project/"lean-toolchain").read_text().strip()
        evidence["generic_checker_source_sha256"] = hashlib.sha256(
            (args.lean_project/"Mettapedia"/"GSLT"/"Parsing"/"SortedCodecMetadataAdmission.lean").read_bytes()).hexdigest()
        evidence["claim"] = "Actual exported finite metadata satisfies the independent Valid schema contract. Native/source adequacy and lost origins are not established."
        (output/"summary.json").write_text(json.dumps(evidence,indent=2)+"\n")
        print("Metadata admission: "+"; ".join(f"{p} {len(d['sorts'])} sorts / {len(d['categories'])} categories / {len(d['productions'])} occurrences" for p,d in exports.items())+
              f"; {len(audits)} kernel-checked axiom audits; {evidence['lean_seconds']:.2f}s")
        return 0
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        print("Metadata admission refused: "+str(error),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
