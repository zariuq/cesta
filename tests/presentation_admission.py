"""Kernel-check actual template data and independently replay native case expansions."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess

import metadata_admission as metadata
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = Path(__file__).resolve().parent.parent


def ls(text):
    return metadata.lean_string(text)


def template(body):
    if set(body) == {"slot"}:
        return f"(.slot {body['slot']})"
    children = ", ".join(template(child) for child in body["children"])
    if "constructor" in body:
        return f"(.node {ls(body['constructor'])} [{children}])"
    if "list" in body:
        return f"(.seq [{children}])"
    raise ValueError("unknown native template")


def prepared_entries(schema):
    categories = {row["name"]: row["sort"] for row in schema["categories"]}
    constructors = defaultdict(list)
    for row in schema["productions"]:
        if row["projection"]["kind"] == "constructor":
            result = categories[row["resultCategory"]]
            arguments = [categories[slot["category"]] for slot in row["rhs"] if slot["kind"] == "argument"]
            constructors[result].append("⟨"+ls(row["projection"]["head"])+", ["+", ".join(map(ls, arguments))+"]⟩")
    entries = []
    for row in schema["sorts"]:
        shape = row["shape"]
        if shape["kind"] == "token":
            value = ".token ."+shape["representation"]
        elif shape["kind"] == "list":
            value = ".list "+ls(shape["element"])
        else:
            value = ".constructors ["+", ".join(constructors[row["name"]])+"]"
        entries.append("⟨"+ls(row["name"])+", "+value+"⟩")
    return "["+", ".join(entries)+"]"


def lowered_rows(schema):
    categories = {row["name"]: row["sort"] for row in schema["categories"]}
    rows = []
    for i, row in enumerate(schema["productions"]):
        if row["projection"]["kind"] == "constructor":
            arguments = [categories[slot["category"]] for slot in row["rhs"] if slot["kind"] == "argument"]
            declaration = "⟨"+ls(row["projection"]["head"])+", ["+", ".join(map(ls, arguments))+"]⟩"
            rows.append("⟨actualMetadataRow"+str(i)+", "+ls(categories[row["resultCategory"]])+", "+declaration+"⟩")
    return "["+", ".join(rows)+"]"


class Adapter:
    def __init__(self, schema, rules):
        self.shapes = {row["name"]: row["shape"] for row in schema["sorts"]}
        cats = {row["name"]: row["sort"] for row in schema["categories"]}
        self.constructors = {}
        for row in schema["productions"]:
            if row["projection"]["kind"] == "constructor":
                self.constructors[row["projection"]["head"]] = (
                    cats[row["resultCategory"]],
                    [cats[slot["category"]] for slot in row["rhs"] if slot["kind"] == "argument"])
        # A list sort admits the empty list only through its own empty production.
        self.empty_lists = {cats[row["resultCategory"]] for row in schema["productions"] if row["projection"]["kind"] == "listEmpty"}
        self.rules = rules["rules"]
        self.tokens = defaultdict(set)
        self.lexer = rules["lexer"]
        self.token_cache = {}

    def accepts_lexical(self, value, rule):
        sort = rule["arguments"][0]
        representation = self.shapes[sort]["representation"]
        if representation == "literal" and not isinstance(value, Text):
            return False
        if representation == "identifier":
            if not isinstance(value, Sym) and not (isinstance(value, list) and len(value) == 2 and value[0] == Sym("c:Symbol")):
                return False
        try:
            spelling = self.token(value)
        except (ValueError, UnicodeDecodeError):
            return False
        if isinstance(value, Sym) and (not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", spelling) or spelling in {"True", "False", "PI", "EXP"}):
            return False
        def contains(body):
            return body.get("constructor") if any("slot" in child for child in body.get("children", [])) else next(
                (found for child in body.get("children", []) if (found := contains(child))), None)
        parent = contains(rule["body"])
        key = (sort, spelling, parent)
        if key in self.token_cache:
            return self.token_cache[key]
        states = self.lexer["states"]

        def closure(initial):
            result, pending = set(), list(initial)
            while pending:
                at = pending.pop()
                if at < 0 or at in result:
                    continue
                result.add(at)
                if states[at][0]:
                    pending.extend(states[at][1:3])
            return result

        def match(token):
            active = closure([token["start"]])
            for byte in spelling.encode():
                active = closure(states[at][1] for at in active if not states[at][0] and
                    int(states[at][3][byte//64], 16) & (1 << (byte%64)))
            return token["end"] in active

        accepted = spelling not in self.lexer["keywords"] and next(
            (token["sort"] for token in self.lexer["tokens"] if match(token)), None) == sort
        if not accepted:
            accepted = any(alias["constructor"] == parent and alias["spelling"] == spelling and
                any(token["name"] == alias["token"] and token["sort"] == sort and match(token)
                    for token in self.lexer["tokens"]) for alias in self.lexer["aliases"])
        self.token_cache[key] = accepted
        return accepted

    def token(self, value):
        if isinstance(value, Text):
            return value.data.decode("utf-8")
        if isinstance(value, Sym):
            return str(value)
        if len(value) == 2 and value[0] == Sym("c:Symbol") and isinstance(value[1], Text):
            return value[1].data.decode("utf-8")
        raise ValueError("unknown token notation")

    def ast(self, value, sort):
        shape = self.shapes[sort]
        if shape["kind"] == "token":
            spelling = self.token(value)
            self.tokens[sort].add(spelling)
            return f"(.text {ls(spelling)})"
        if shape["kind"] == "list":
            assert value[0] == Sym("c:List")
            return "(.seq ["+", ".join(self.ast(child, shape["element"]) for child in value[1:])+"])"
        name = str(value[0])[2:]
        result, sorts = self.constructors[name]
        assert result == sort and len(sorts) == len(value)-1
        return f"(.node {ls(name)} ["+", ".join(self.ast(child, child_sort) for child, child_sort in zip(value[1:], sorts))+"])"

    def surface(self, value, sort):
        shape = self.shapes[sort]
        if shape["kind"] == "token":
            try:
                return f"(.text {ls(self.token(value))})"
            except (ValueError, UnicodeDecodeError, TypeError) as error:
                raise Refused("token notation") from error
        if isinstance(value, list) and value and isinstance(value[0], Sym) and value[0].startswith("c:") and value[0] != Sym("c:Symbol"):
            name = str(value[0])[2:]
            if shape["kind"] == "list":
                if name != "List":
                    raise Refused("exact list head")
                return "(.seq ["+", ".join(self.surface(child, shape["element"]) for child in value[1:])+"])"
            if name not in self.constructors:
                raise Refused("unknown exact constructor")
            result, sorts = self.constructors[name]
            if result != sort or len(sorts) != len(value)-1:
                raise Refused("ill-sorted exact constructor")
            return f"(.node {ls(name)} ["+", ".join(self.surface(child, child_sort) for child, child_sort in zip(value[1:], sorts))+"])"
        if shape["kind"] == "list":
            if not isinstance(value, list) or (not value and sort not in self.empty_lists):
                raise Refused("list sort requires an admitted list")
            return "(.seq ["+", ".join(self.surface(child, shape["element"]) for child in value)+"])"
        # Every rule whose shape matches is tried in full; exactly one reading
        # may succeed. Unheaded rules are defaults behind every other rule.
        rules = [rule for rule in self.rules if rule["result"] == sort]
        for group in ([rule for rule in rules if rule["shape"] != 3], [rule for rule in rules if rule["shape"] == 3]):
            readings = []
            for rule in group:
                args = self.arguments(rule, value)
                if args is None:
                    continue
                try:
                    children = ", ".join(self.surface(child, child_sort) for child, child_sort in zip(args, rule["arguments"]))
                except Refused:
                    continue
                # Native sort/head/arity resolution selects a declaration identity.
                # That identity is the key of the subsequent structural tree model.
                readings.append(f"(.form {ls(rule['name'])} [{children}])")
            if len(readings) > 1:
                raise AssertionError("nonunique independent dispatch")
            if readings:
                return readings[0]
        raise Refused("no presentation reading")

    def arguments(self, rule, value):
        """The rule's slot values read from one atom, or None when its shape does not match."""
        shape = rule["shape"]
        if shape == 3:
            return [value]
        if shape == 1:
            return [] if isinstance(value, Sym) and str(value) == rule["head"] else None
        if shape == 2:
            return [] if value == [] else None
        if shape == 5:
            return [value] if self.accepts_lexical(value, rule) else None
        if shape == 6:
            head = rule["head"]
            if not head:
                # A token written as the atom its spelling reads as.
                if isinstance(value, Text):
                    spelling = quote(value.data)
                elif isinstance(value, Sym):
                    spelling = str(value)
                else:
                    return None
                child = Text(spelling.encode())
            elif isinstance(value, Sym) and value.startswith(head) and len(value) > len(head):
                spelling = str(value)[len(head):]
                child = Sym(spelling) if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", spelling) and spelling not in {"True", "False", "PI", "EXP"} else [Sym("c:Symbol"), Text(spelling.encode())]
            else:
                return None
            return [child] if self.accepts_lexical(child, rule) else None
        if not isinstance(value, list):
            return None
        prefix = 1 if shape == 0 else 0
        if prefix and (not value or value[0] != Sym(rule["head"])):
            return None
        items = value[prefix:]
        fields = rule.get("fields", list(range(len(rule["arguments"]))))
        n, count = len(items), len(fields)
        splice, dotted = rule["splice"], rule.get("dotted", False)
        last = max(i for i, field in enumerate(fields) if isinstance(field, int)) if any(isinstance(field, int) for field in fields) else -1
        suffix = count-1-last if splice else 0
        if (splice and n < count-1) or (dotted and n < count) or (not splice and not dotted and n != count):
            return None
        args = [None]*len(rule["arguments"])
        for i, field in enumerate(fields):
            if splice and i == last:
                item = list(items[i:n-suffix])
            elif splice and i > last:
                item = items[n-(count-i)]
            elif dotted and i == count-1:
                item = items[i] if n == count else list(items[i:])
            else:
                item = items[i]
            if isinstance(field, int):
                args[field] = item
            elif item != Sym(field):
                return None
        return args


class Refused(Exception):
    pass


def quote(data):
    """The printed form of a string atom, as the native writer prints it."""
    out = ['"']
    for byte in data:
        if byte in (34, 92):
            out.append("\\"+chr(byte))
        elif byte == 10:
            out.append("\\n")
        elif byte == 13:
            out.append("\\r")
        elif byte == 9:
            out.append("\\t")
        elif byte < 32 or byte >= 127:
            out.append("\\x%02x" % byte)
        else:
            out.append(chr(byte))
    return "".join(out)+'"'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-memory-mb", type=int)
    parser.add_argument("--input-prefix", default="presentation-kernel")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", type=Path, default=ROOT / "build/presentation-admission")
    args = parser.parse_args()
    output = args.output.resolve()
    assert output.is_relative_to(ROOT / "build")
    output.mkdir(parents=True, exist_ok=True)
    paths = {kind: ROOT / ("build/"+args.input_prefix+"-"+kind+".json") for kind in ("rules", "schema", "cases")}
    snapshots = {kind: path.read_bytes() for kind, path in paths.items()}
    rules, schema, cases = (json.loads(snapshots[kind]) for kind in ("rules", "schema", "cases"))
    metadata.validate_json(schema)
    assert rules["format"] == "cesta.presentation-rules.v1"
    adapter = Adapter(schema, rules)
    controls = []
    for case in cases:
        ast = adapter.ast(parse(case["ast"].encode())[0], case["sort"])
        surface = adapter.surface(parse(case["surface"].encode())[0], case["sort"])
        controls.append((case["sort"], surface, ast))
    # Asynchronous elaboration retains every proof's state until the end of the file, so
    # memory grows with the number of admitted templates; sequential elaboration stays flat.
    code = ["""import Mettapedia.GSLT.Parsing.SortedTreePresentationTemplates
import Mettapedia.GSLT.Parsing.SortedCodecMetadataAdmission
import Mettapedia.GSLT.Parsing.PreparedSortedCodecSchema
set_option autoImplicit false
set_option maxRecDepth 65536
set_option maxHeartbeats 0
set_option Elab.async false
namespace CestaNativePresentationAdmission
open Mettapedia.GSLT.Parsing
open SortedCodecMetadataAdmission
open SortedCanonicalSExprCodec
open CheckedTreePresentation (Template Rule Surface instantiate expand)
local instance : DecidableEq CanonicalGrammar.CanonicalTerm :=
  CheckedTreePresentation.decidableTreeEquality
def actualNotation : Notation where
  safeSymbol spelling := spelling != "True" && spelling != "False"
""", metadata.metadata_definition("actualMetadata", schema)]
    pairs = [(sort, spelling) for sort, spellings in sorted(adapter.tokens.items()) for spelling in sorted(spellings)]
    code.append("def sampledTokens : List (String × String) := ["+", ".join(f"({ls(sort)}, {ls(spelling)})" for sort, spelling in pairs)+"]")
    code.append("def sampledLexicalPolicy (sort spelling : String) : Bool := sampledTokens.contains (sort, spelling)")
    code.append("def actualSchema : Schema := schema actualMetadata sampledLexicalPolicy")
    code.append("def actualLowered : List LoweredConstructor := "+lowered_rows(schema))
    code.append("theorem actualLowered_checked : constructorRows actualMetadata = actualLowered := by rfl")
    code.append('#eval IO.println "constructor rows lowered and checked"')
    code.append("def actualPrepared : List PreparedSortedCodecSchema.Entry := "+prepared_entries(schema))
    code.append("theorem actualPrepared_checked : PreparedSortedCodecSchema.prepare actualMetadata = actualPrepared := by\n  change PreparedSortedCodecSchema.prepareUsing actualMetadata (constructorRows actualMetadata) = actualPrepared\n  rw [actualLowered_checked]\n  rfl")
    code.append('#eval IO.println "prepared shape table checked"')
    code.append("def cachedSchema : Schema := PreparedSortedCodecSchema.preparedSchema actualPrepared sampledLexicalPolicy")
    code.append("theorem cachedSchema_correct : cachedSchema = actualSchema := by\n  rw [cachedSchema, ← actualPrepared_checked]\n  exact PreparedSortedCodecSchema.preparedSchema_eq _ _")
    audits = ["actualLowered_checked", "actualPrepared_checked", "cachedSchema_correct"]
    rule_rows = []
    for i, rule in enumerate(rules["rules"]):
        stem = "rule"+str(i)
        slots = "["+", ".join(ls(sort) for sort in rule["arguments"])+"]"
        code.append(f"def {stem}Body : Template := {template(rule['body'])}")
        code.append(f"def {stem}Slots : List String := {slots}")
        code.append(f"theorem {stem}_checked : SortedTreePresentationTemplates.check actualSchema actualNotation {stem}Slots {ls(rule['result'])} {stem}Body = true := by\n  rw [← cachedSchema_correct]\n  decide +kernel")
        code.append(f"theorem {stem}_typed : SortedTreePresentationTemplates.Typed actualSchema actualNotation {stem}Slots {ls(rule['result'])} {stem}Body :=\n  SortedTreePresentationTemplates.check_sound _ _ _ _ _ {stem}_checked")
        code.append(f"theorem {stem}_instantiates (values : List CanonicalGrammar.CanonicalTerm)\n    (valuesTyped : SortedArguments actualSchema actualNotation {stem}Slots values) :\n    ∃ result, instantiate values {stem}Body = some result ∧ Sorted actualSchema actualNotation {ls(rule['result'])} result :=\n  SortedTreePresentationTemplates.checked_instantiation_sorted _ _ _ _ _ _ {stem}_checked valuesTyped")
        audits += [stem+"_checked", stem+"_typed", stem+"_instantiates"]
        rule_rows.append("⟨"+ls(rule["name"])+", "+str(len(rule["arguments"]))+", "+stem+"Body⟩")
        if (i+1) % 25 == 0:
            code.append(f'#eval IO.println "{i+1} template rules checked"')
    code.append("def actualRules : List Rule := ["+", ".join(rule_rows)+"]")
    for i, (sort, surface, ast) in enumerate(controls):
        stem = "case"+str(i)
        code.append(f"def {stem}Surface : Surface := {surface}")
        code.append(f"def {stem}Ast : CanonicalGrammar.CanonicalTerm := {ast}")
        code.append(f"theorem {stem}_native_agreement : expand actualRules {stem}Surface = some {stem}Ast := by decide +kernel")
        audits.append(stem+"_native_agreement")
    # Actual altered templates fail against the independently extracted schema.
    for stem, slots, sort, body in [
        ("wrong_slot_sort", '["Statement"]', "Expr", '(.node "Indirection" [.slot 0])'),
        ("missing_slot", '[]', "Expr", '(.node "Indirection" [.slot 0])'),
        ("wrong_result", '[]', "Expr", '(.node "NoFullExpression" [])'),
        ("unknown_constructor", '[]', "Expr", '(.node "ImaginaryConstructor" [])'),
        ("wrong_constructor_arity", '["Expr"]', "Expr", '(.node "Add" [.slot 0])'),
    ]:
        code.append(f"theorem {stem} : SortedTreePresentationTemplates.check actualSchema actualNotation {slots} {ls(sort)} {body} = false := by\n  rw [← cachedSchema_correct]\n  decide +kernel")
        audits.append(stem)
    code += ["#print axioms "+name for name in audits]
    code.append("end CestaNativePresentationAdmission\n")
    artifact = output / "ActualPresentationAdmission.lean"
    artifact.write_text("\n\n".join(code))
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb:
        command += ["-M", str(args.lean_memory_mb)]
    project = ROOT.parent / "Mettapedia/lean/mettapedia"
    with (output / "kernel.log").open("wb") as log:
        with subprocess.Popen([*command, str(artifact)], cwd=project, stdout=log, stderr=subprocess.STDOUT) as process:
            try:
                status = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
    evidence = (output / "kernel.log").read_text()
    if status:
        raise RuntimeError(evidence)
    for match in re.finditer(r"depends on axioms:\s*\[(.*?)\]", evidence, re.DOTALL):
        assert {item.strip() for item in match[1].split(",") if item.strip()} <= metadata.ALLOWED_AXIOMS
    reported = set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)", evidence))
    assert reported == {"CestaNativePresentationAdmission."+name for name in audits}, "axiom audit must cover every generated theorem"
    for kind, path in paths.items():
        assert path.read_bytes() == snapshots[kind], "native evidence changed during kernel verification"
    summary = {"rules": len(rules["rules"]), "native_cases": len(controls), "negative_controls": 5,
               "theorems": len(audits), "inputs": {kind: hashlib.sha256(data).hexdigest() for kind, data in snapshots.items()},
               "boundary": f"Actual template sorting and {len(controls)} native/model expansions. Sampled lexical policy and independent source adapter; no universal native reader/interpreter, C binding or source adequacy proof."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(f"presentation admission: {summary['rules']} actual typed templates; {summary['native_cases']} native/model cases; 5 negative controls; {summary['theorems']} kernel theorems")


if __name__ == "__main__":
    main()
