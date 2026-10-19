"""Kernel admission of every readable export Cesta actually wrote.

For each use in the export plans of the library program and of the control
program, Lean decides `admitUse`: the recorded target is exactly the lowering
of the source rule and arguments, the rule lies in the fragment
`recorded_export_correct` covers, and the picked names are distinct. The
theorem then says the recorded C behaves as the source use. Each use is also
checked for hygiene against its written arguments, and mutated targets that
keep every name right are refused.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess

from metadata_admission import lean_string
from oracles.pro_compact_cesta import Sym, Text, parse
import macro_program_export

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/export-admission"
LEAN = ROOT.parent / "Mettapedia/lean/mettapedia"
EXPORT_CONTROLS = ROOT / "tests/modules/MacroExportControls.module"
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def use(module, theory):
    return f'(use-macros {json.dumps(str(module))} {theory})'


# Uses the library program does not make: a renamed rule, uses composed inside
# another use's arguments, a place argument whose result needs a temporary, a
# block template with an initialized local, and a result type written with a
# different spelling than the local that holds it.
CONTROL_PROGRAM = ('(cesta native-c/1 compact-c/2 (unit (include <stdint.h>) '
                   + use(macro_program_export.CONTROLS, "Arithmetic") + ' '
                   + use(EXPORT_CONTROLS, "Controls") + ' '
                   '(native "typedef int counter_t; uint64_t input(void);") '
                   '(fn int composed ((int a) (int b) (int c)) (return (ordered-sum (ordered-sum a b) (twice-once c)))) '
                   '(fn int local ((int a)) (return (local-plus-one a))) '
                   '(fn int bump ((int (* p))) (return (increment-place (* p)))) '
                   '(fn int renamed ((uint32_t (* out))) (return (renamed-narrow uint64_t uint32_t (call input) (* out)))) '
                   '(fn int declared ((counter_t v)) (return (as-declared int counter_t v))) '
                   '(native ' + json.dumps(r'''
uint64_t input(void) { return 5; }
int main(void) {
    int x = 7; uint32_t out = 0;
    if (composed(1, 2, 3) != 9) return 1;
    if (local(4) != 5) return 2;
    if (bump(&x) != 8 || x != 8) return 3;
    if (!renamed(&out) || out != 5) return 4;
    if (declared(9) != 9) return 5;
    return 0;
}
''') + ')))\n')

LIBRARY_RULES = (("tryNarrow", "TryNarrow", macro_program_export.MACROS / "Narrowing.module", "Checked"),
                  ("arrayBytes", "ArrayBytes", macro_program_export.MACROS / "Sizes.module", "Checked"),
                  ("doubledCapacity", "DoubledCapacity", macro_program_export.MACROS / "Sizes.module", "Checked"),
                  ("push", "Push", macro_program_export.MACROS / "Worklists.module", "Growable"),
                  ("probe", "Probe", macro_program_export.MACROS / "Probes.module", "Switched"))
LIBRARY_LAWS = ("tryNarrow_export", "arrayBytes_export", "doubledCapacity_export", "probe_export", "push_export",
                "push_aliased", "push_overfull", "push_unallocated", "push_room", "push_doubling_overflows",
                "push_bytes_overflow", "push_resize_refused", "push_resized",
                "tryNarrow_forms", "arrayBytes_forms", "doubledCapacity_forms", "push_forms", "probe_forms")

HEADER_SPECS = ("narrowing", "sizes", "worklists", "probes")
HEADER_LAWS = ("function_correct", "recorded_function_correct", "function_as_use")
LIBRARY_LAWS += ("tryNarrow_function", "arrayBytes_function", "doubledCapacity_function", "push_function",
                 "probe_macro")


def deref_parameter(definition, _):
    """Declare the first place parameter as its target instead of a pointer to it."""
    if not headed(definition, "fn") or len(definition) < 4: return definition, False
    params = definition[3]
    for i, p in enumerate(params):
        if isinstance(p, list) and len(p) == 2 and headed(p[1], "*"):
            changed = list(params); changed[i] = [p[0], p[1][1]]
            return definition[:3] + [changed] + definition[4:], True
    return definition, False


def returned(definition, _):
    """Return a different local than the template's result."""
    if not headed(definition, "fn"): return definition, False
    last = definition[-1]
    if headed(last, "return") and len(last) == 2 and isinstance(last[1], Sym):
        names = [x[2] for x in definition[4:-1] if headed(x, "var") and len(x) >= 3 and isinstance(x[2], Sym)]
        others = [n for n in names if n != last[1]]
        if others: return definition[:-1] + [[last[0], others[0]]], True
    return definition, False


def unchecked_switch(expression, _):
    """Drop the probe macro's static check."""
    if headed(expression, ",") and len(expression) == 3: return expression[2], True
    return expression, False


HEADER_MUTATIONS = {"store": None, "guard": None, "branches": None, "intrinsic": None, "callee": None,
                    "rename": None, "parameter": deref_parameter, "return": returned, "static-check": unchecked_switch}

MODULES = ((macro_program_export.MACROS / "Narrowing.module", "Checked"),
           (macro_program_export.MACROS / "Sizes.module", "Checked"),
           (macro_program_export.MACROS / "Worklists.module", "Growable"),
           (macro_program_export.MACROS / "Probes.module", "Switched"),
           (macro_program_export.CONTROLS, "Arithmetic"),
           (EXPORT_CONTROLS, "Controls"))


def symbol(a):
    if not isinstance(a, Sym): raise ValueError("expected a symbol")
    return str(a)


def text(a):
    if not isinstance(a, Text): raise ValueError("expected a text")
    return a.data.decode("utf-8")


def atom(a):
    """An atom as Lean `MacroExportPlan.Atom` or `MacroTemplateNames.Atom` syntax."""
    if isinstance(a, Sym): return ".symbol " + lean_string(str(a))
    if isinstance(a, Text): return ".text " + lean_string(a.data.decode("utf-8"))
    return ".list [" + ", ".join("(" + atom(x) + ")" for x in a) + "]"


def headed(a, name):
    return isinstance(a, list) and bool(a) and isinstance(a[0], Sym) and str(a[0]) == name


def first(a, pred, change):
    """Replace the first subterm, in preorder, that satisfies `pred`."""
    if pred(a): return change(a), True
    if isinstance(a, list):
        out, done = [], False
        for x in a:
            if done: out.append(x)
            else:
                y, done = first(x, pred, change); out.append(y)
        return out, done
    return a, False


def acquisitions(target):
    return [i for i, x in enumerate(target) if headed(x, "var") and len(x) == 4 and x[1] == Sym("__auto_type")]


def swapped(target):
    found = acquisitions(target)
    if not headed(target, "block") or len(found) < 2: return target, False
    out = list(target); i, j = found[:2]; out[i], out[j] = out[j], out[i]
    return out, True


def duplicated(target):
    found = acquisitions(target)
    if not headed(target, "block") or not found: return target, False
    i = found[0]
    return target[:i + 1] + [target[i]] + target[i + 1:], True


def hoisted(target):
    if not headed(target, "block") or not headed(target[-1], "block") or not acquisitions(target):
        return target, False
    return target[:-1] + target[-1][1:], True


def unchecked(target):
    def result_check(a):
        return headed(a, "_Static_assert") and isinstance(a[-1], Text) and b"result has its declared type" in a[-1].data
    def drop(a):
        if isinstance(a, list):
            kept = [x for x in a if not result_check(x)]
            if len(kept) != len(a): return kept, True
            out, done = [], False
            for x in a:
                if done: out.append(x)
                else:
                    y, done = drop(x); out.append(y)
            return out, done
        return a, False
    return drop(target)


def renamed(target, picked):
    if len(picked) < 2: return target, False
    return first(target, lambda a: a == Sym(picked[0]), lambda a: Sym(picked[1]))


BUILTINS = ("__builtin_add_overflow", "__builtin_mul_overflow")
MUTATIONS = {
    "store": lambda t, _: first(t, lambda a: headed(a, "=") and len(a) == 3 and headed(a[1], "*"),
                                lambda a: [a[0], a[2], a[2]]),
    "guard": lambda t, _: first(t, lambda a: headed(a, "if") and len(a) == 3, lambda a: a[2]),
    "branches": lambda t, _: first(t, lambda a: headed(a, "?:") and len(a) == 4 and a[2] != a[3],
                                   lambda a: [a[0], a[1], a[3], a[2]]),
    "intrinsic": lambda t, _: first(t, lambda a: isinstance(a, Sym) and str(a) in BUILTINS,
                                    lambda a: Sym(BUILTINS[1 - BUILTINS.index(str(a))])),
    "callee": lambda t, _: first(t, lambda a: headed(a, "call") and len(a) > 1 and isinstance(a[1], Sym)
                                 and str(a[1]) not in BUILTINS + ("__builtin_constant_p",),
                                 lambda a: [a[0], Sym("abort")] + a[2:]),
    "order": lambda t, _: swapped(t),
    "duplicate": lambda t, _: duplicated(t),
    "hoist": lambda t, _: hoisted(t),
    "result-check": lambda t, _: unchecked(t),
    "rename": lambda t, picked: renamed(t, picked),
}


def term(a, names, locals_, acquired, builtins):
    """The rule template with acquired arguments read from their locals."""
    if isinstance(a, Sym): return ".symbol " + lean_string(str(a))
    if isinstance(a, Text): return ".text " + lean_string(a.data.decode("utf-8"))
    head = symbol(a[0]) if a and isinstance(a[0], Sym) else ""
    if head == "Argument":
        i = names.index(symbol(a[1]))
        return f".bound ⟨{acquired[i]}, by decide⟩" if i in acquired else f".argument ⟨{i}, by decide⟩"
    if head == "TypeArgument":
        return f".typeArgument ⟨{names.index(symbol(a[1]))}, by decide⟩"
    if head == "LocalRef":
        index = locals_.index((symbol(a[2]), a[1].data.decode("utf-8")))
        return f".bound ⟨{len(acquired) + index}, by decide⟩"
    if head == "CompilerRef":
        return ".symbol " + lean_string(builtins[symbol(a[1])])
    if head in ("Capture", "DefinitionRef"):
        raise ValueError(head + " is not part of readable export")
    return ".list [" + ", ".join("(" + term(x, names, locals_, acquired, builtins) + ")" for x in a) + "]"


def hygiene(k, record, rule):
    """The names a use picked are visible nowhere in its written arguments."""
    label = text(record[2][3]); written = record[3][1:]; chosen = record[4][1:]
    local_names = [text(x) for x in record[5][1:]]
    contract = rule[4]; ports = len(contract) == 8
    args = contract[3 + ports][1:]; names = [symbol(arg[1]) for arg in args]
    locals_ = [(symbol(local[1]), text(local[2])) for local in contract[5 + ports][1:]]
    builtins = {symbol(port[1]): text(port[2])
                for port in (contract[3][1:] if ports else []) if port[0] == Sym("CompilerBuiltin")}
    acquired = {}; acquired_names = []
    for i, name in enumerate(chosen):
        if not (isinstance(name, Sym) and str(name) == "-"):
            acquired[i] = len(acquired_names); acquired_names.append(symbol(name))
    body = term(contract[6 + ports][1], names, locals_, acquired, builtins)
    prefix = [f".list [.symbol \"var\", .symbol \"__auto_type\", .bound ⟨{acquired[i]}, by decide⟩, "
              f".argument ⟨{i}, by decide⟩]" for i in sorted(acquired)]
    nlocals = len(acquired_names) + len(local_names)
    actual = "![" + ", ".join("(" + atom(x) + ")" for x in written) + "]"
    emitted = "![" + ", ".join(lean_string(x) for x in acquired_names + local_names) + "]"
    lines = [f"def names{k} : Template {len(names)} {nlocals} := .list [" +
             ", ".join("(" + x + ")" for x in prefix + [body]) + "]",
             f"-- {label}: {' '.join(acquired_names + local_names)}",
             f"example : checkNames ({actual} : Fin {len(names)} → Atom) "
             f"({emitted} : Fin {nlocals} → String) names{k} = true := by decide"]
    # Control: a name taken from the caller's own argument is refused.
    caller = [str(x) for x in written if isinstance(x, Sym) and str(x) not in names]
    if acquired_names and caller:
        clashing = "![" + ", ".join(lean_string(x) for x in [caller[0]] + acquired_names[1:] + local_names) + "]"
        lines.append(f"example : checkNames ({actual} : Fin {len(names)} → Atom) "
                     f"({clashing} : Fin {nlocals} → String) names{k} = false := by decide")
        return lines, True
    return lines, False


def lean_command(path, memory_mb=None):
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if memory_mb is not None:
        command += ["-M", str(memory_mb)]
    return [*command, str(path)]


def kernel(path, lines, memory_mb=None):
    path.write_text("\n".join(lines) + "\n")
    result = subprocess.run(lean_command(path, memory_mb), cwd=LEAN, capture_output=True)
    path.with_suffix(".log").write_bytes(result.stdout + result.stderr)
    if result.returncode or b"warning:" in result.stdout or b"sorry" in result.stdout:
        raise RuntimeError((result.stdout + result.stderr).decode(errors="replace")[-8000:])
    return result.stdout.decode()


def axioms(output):
    for line in output.split("depends on axioms:")[1:]:
        found = set(x.strip(" \n,'") for x in line.split("]")[0].strip(" [").split(","))
        assert found <= STANDARD_AXIOMS, f"unexpected axioms {found}"
    assert "sorryAx" not in output


def refusal_controls(memory_mb):
    """The former counterexamples fail the appropriate semantic admission."""
    lines = ["import Mettapedia.GSLT.LanguageDef.MacroExportComplete",
             "import Mettapedia.GSLT.LanguageDef.MacroExportOrder",
             "open Mettapedia.GSLT.LanguageDef.MacroExportPlan",
             "open Mettapedia.GSLT.LanguageDef.MacroExportTheorem",
             "open Mettapedia.GSLT.LanguageDef.MacroExportHeader",
             "open Mettapedia.GSLT.LanguageDef.MacroExportOrder"]
    data, = parse(subprocess.check_output([str(BINARY), "check-macros", str(macro_program_export.CONTROLS),
                                          "Arithmetic"], cwd=ROOT))
    evaluated = ("Twice", "Ordered", "Increment", "Lazy", "Repeated")
    for i, label in enumerate(evaluated):
        rule = next(r for r in data[2:] if text(r[1]) == label)
        lines.append(f"def evaluatedRule{i} : Atom := {atom(rule)}")
        lines.append(f"example : (decodeRule evaluatedRule{i}).map "
                     "Mettapedia.GSLT.LanguageDef.MacroExportComplete.macroForm = some false := by decide")
        lines.append(f'example : (decodeRule evaluatedRule{i}).bind (fun r => lowerMacro r "ct_bad") = none '
                     ':= by decide')
    # The exact old header record was structurally accepted despite duplicating
    # acquisition. Refuse it, not only the native export request.
    bad_header = [Sym("header"), Sym("cesta-header-plan/1"),
                  [Sym("rule"), Text(str(macro_program_export.CONTROLS).encode()), Text(b"Arithmetic"), Text(b"Twice")],
                  [Sym("macro"), Sym("ct_twice")], [Sym("types")], [Sym("picked"), Text(b"x")],
                  [Sym("expression"), [Sym("+"), Sym("x"), Sym("x")]]]
    lines.append(f"example : checkHeader evaluatedRule0 ({atom(bad_header)}) = false := by decide")
    data, = parse(subprocess.check_output([str(BINARY), "check-macros", str(EXPORT_CONTROLS), "EvaluationOrder"],
                                          cwd=ROOT))
    for i, (label, head, accepted) in enumerate((("ConflictingUpdate", "conflicting-update", False),
                                                ("StableUpdate", "stable-update", True))):
        rule = next(r for r in data[2:] if text(r[1]) == label)
        source = WORK / (head + ".cesta"); target = WORK / (head + ".c"); plan = WORK / (head + ".plan")
        source.write_text('(cesta native-c/1 compact-c/2 (unit ' + use(EXPORT_CONTROLS, "EvaluationOrder") +
            f' (fn int update ((int seed)) (return ({head} seed))) '
            '(native "int main(void) { return update(1) == 2 ? 0 : 1; }")))\n')
        subprocess.run([str(BINARY), "export-c", "--cc", "gcc", "--plan", str(plan), str(source), "-o", str(target)],
                       cwd=WORK, check=True, capture_output=True)
        record, = parse(plan.read_bytes())
        lines += [f"def orderRule{i} : Atom := {atom(rule)}", f"def orderUse{i} : Atom := {atom(record)}",
                  f"example : admitUse orderRule{i} orderUse{i} = true := by decide",
                  f"example : ((decodeRule orderRule{i}).bind fun r => (decodeUse orderUse{i}).bind fun u => "
                  f"(lower r u.written u.picked).map disciplined) = some {'true' if accepted else 'false'} := by decide"]
        for cc in ("gcc", "clang"):
            exe = WORK / (head + "-" + cc)
            native = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(target), "-o", str(exe)],
                                    capture_output=True)
            if accepted:
                assert native.returncode == 0, native.stderr
                subprocess.run([str(exe)], check=True)
            else:
                assert native.returncode != 0, "native compiler accepted conflicting compound assignment"
                assert (b"unsequenced" if cc == "clang" else b"sequence-point") in native.stderr, native.stderr
    kernel(WORK / "refusal-controls.lean", lines, memory_mb)
    return {"evaluated_macro_rules_refused": len(evaluated), "old_duplicating_header_refused": True,
            "conflicting_compound_assignment_refused": True, "stable_compound_assignment_accepted": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-memory-mb", type=int, help="optional Lean memory limit in MiB")
    options = parser.parse_args()
    if options.lean_memory_mb is not None and options.lean_memory_mb <= 0:
        parser.error("--lean-memory-mb must be positive")
    WORK.mkdir(parents=True, exist_ok=True)
    refusals = refusal_controls(options.lean_memory_mb)
    rules = {}
    for module, theory in MODULES:
        metadata = subprocess.run([str(BINARY), "check-macros", str(module), theory], cwd=ROOT,
                                  check=True, capture_output=True).stdout
        data, = parse(metadata)
        for rule in data[2:]:
            rules[(str(module), theory, text(rule[1]))] = rule
    uses = []
    for name, program in (("library", macro_program_export.PROGRAM), ("controls", CONTROL_PROGRAM)):
        source = WORK / f"{name}.cesta"; source.write_text(program)
        plan = WORK / f"{name}.plan"; exported = WORK / f"{name}.c"
        subprocess.run([str(BINARY), "export-c", "--cc", "gcc", "--plan", str(plan), str(source), "-o", str(exported)],
                       cwd=WORK, check=True, capture_output=True)
        records = parse(plan.read_bytes())
        assert records, f"the {name} program exported no uses"
        uses += [(name, record) for record in records]
    # The control program's export is plain C both compilers accept and run.
    for cc in ("gcc", "clang"):
        program = WORK / f"controls-{cc}"
        subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(WORK / "controls.c"), "-o", str(program)],
                       check=True, capture_output=True)
        subprocess.run([str(program)], check=True)
    exports = ["import Mettapedia.GSLT.LanguageDef.MacroExportTheorem",
               "import Mettapedia.GSLT.LanguageDef.MacroExportHeader",
               "import Mettapedia.GSLT.LanguageDef.MacroExportComplete",
               "import Mettapedia.GSLT.LanguageDef.MacroExportOrder",
               "open Mettapedia.GSLT.LanguageDef.MacroExportPlan Mettapedia.GSLT.LanguageDef.MacroExportTheorem",
               "set_option autoImplicit false", "set_option maxRecDepth 20000",
               "namespace CestaActualExports"]
    names = [f"import Mettapedia.GSLT.LanguageDef.MacroTemplateNames", "import Mathlib.Data.Fin.VecNotation",
             "open Mettapedia.GSLT.LanguageDef.MacroTemplateNames",
             "set_option autoImplicit false", "set_option Elab.async false",
             "namespace CestaActualExportNames"]
    rule_index, report, clashes, refused = {}, [], 0, {kind: 0 for kind in MUTATIONS}
    # Each recorded rule is in the declared forms, so every use of it is admitted, not only these.
    forms, complete = set(), "Mettapedia.GSLT.LanguageDef.MacroExportComplete"
    for k, (program, record) in enumerate(uses):
        if record[0] != Sym("use") or record[1] != Sym("cesta-export-plan/2"):
            raise ValueError("expected a cesta-export-plan/2 use record")
        key = (text(record[2][1]), text(record[2][2]), text(record[2][3]))
        rule = rules[key]
        if key not in rule_index:
            rule_index[key] = len(rule_index)
            exports.append(f"def rule{rule_index[key]} : Atom := {atom(rule)}")
        r = rule_index[key]
        if (r, "lowerable") not in forms:
            forms.add((r, "lowerable"))
            exports.append(f"example : (decodeRule rule{r}).map {complete}.lowerable = some true := by decide")
        exports.append(f"def use{k} : Atom := {atom(record)}")
        exports.append(f"example : admitUse rule{r} use{k} = true := by decide")
        # Every unsequenced operand of the exported block is a simple read.
        exports.append(f"example : ((decodeRule rule{r}).bind fun rule => (decodeUse use{k}).bind fun use => "
                       f"(lower rule use.written use.picked).map "
                       f"Mettapedia.GSLT.LanguageDef.MacroExportOrder.disciplined) = some true := by decide")
        target = record[8][1]; picked = [text(x) for x in record[6][1:]]
        kinds = []
        for kind, mutate in MUTATIONS.items():
            mutant, changed = mutate(target, picked)
            if changed and mutant != target:
                exports.append(f"example : checkUse rule{r} ({atom(record[:8] + [[Sym('target'), mutant]])}) = false"
                               " := by decide")
                kinds.append(kind); refused[kind] += 1
        lines, clash = hygiene(k, record, rule)
        names += lines; clashes += clash
        report.append({"program": program, "rule": key[2], "names": picked, "admitted": True, "in_declared_forms": True,
                       "order_disciplined": True, "hygiene": True, "mutations_refused": kinds})
    # Header definitions: each recorded definition is the lowering of its rule.
    headers, header_report = [], []
    for name in HEADER_SPECS:
        plan = WORK / f"header-{name}.plan"
        subprocess.run([str(BINARY), "export-header", "--plan", str(plan), str(ROOT / "tests/export" / f"{name}.export"),
                        "-o", str(WORK / f"header-{name}.h")], cwd=ROOT, check=True, capture_output=True)
        headers += parse(plan.read_bytes())
    assert headers, "the header specifications exported nothing"
    header_refused = {kind: 0 for kind in HEADER_MUTATIONS}
    for k, record in enumerate(headers):
        if record[0] != Sym("header") or record[1] != Sym("cesta-header-plan/1"):
            raise ValueError("expected a cesta-header-plan/1 record")
        key = (text(record[2][1]), text(record[2][2]), text(record[2][3]))
        rule = rules[key]
        if key not in rule_index:
            rule_index[key] = len(rule_index)
            exports.append(f"def rule{rule_index[key]} : Atom := {atom(rule)}")
        r = rule_index[key]
        form = "functionForm" if symbol(record[3][0]) == "function" else "macroForm"
        if (r, form) not in forms:
            forms.add((r, form))
            exports.append(f"example : (decodeRule rule{r}).map {complete}.{form} = some true := by decide")
        exports.append(f"def header{k} : Atom := {atom(record)}")
        exports.append(f"example : Mettapedia.GSLT.LanguageDef.MacroExportHeader.checkHeader rule{r} header{k} = true "
                       ":= by decide")
        emitted = record[6][1]; picked = [text(x) for x in record[5][1:]]; kinds = []
        for kind, mutate in HEADER_MUTATIONS.items():
            mutant, changed = (mutate or MUTATIONS[kind])(emitted, picked)
            if changed and mutant != emitted:
                exports.append(f"example : Mettapedia.GSLT.LanguageDef.MacroExportHeader.checkHeader rule{r} "
                               f"({atom(record[:6] + [[record[6][0], mutant]])}) = false := by decide")
                kinds.append(kind); header_refused[kind] += 1
        assert kinds, f"no mutation applies to header definition {symbol(record[3][1])}"
        header_report.append({"definition": symbol(record[3][1]), "kind": symbol(record[3][0]), "rule": key[2],
                              "names": picked, "admitted": True, "in_declared_forms": True,
                              "mutations_refused": kinds})
    exports += ["end CestaActualExports",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportTheorem.recorded_export_correct",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportTheorem.export_correct",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportOrder.simple_preserves",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportOrder.values_simple",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportComplete.export_complete",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportComplete.checkHeader_function_complete",
                "#print axioms Mettapedia.GSLT.LanguageDef.MacroExportComplete.checkHeader_macro_complete"] + [
                f"#print axioms Mettapedia.GSLT.LanguageDef.MacroExportHeader.{law}" for law in HEADER_LAWS]
    names += ["end CestaActualExportNames"]
    axioms(kernel(WORK / "actual-exports.lean", exports, options.lean_memory_mb))
    kernel(WORK / "actual-names.lean", names, options.lean_memory_mb)
    missing = [kind for kind, count in refused.items() if count == 0]
    assert not missing, f"no use exercised the mutations {missing}"
    assert clashes, "no use supplied a clashing-name control"
    covered = {rule for (_, _, rule) in rule_index}
    for label in ("TryNarrow", "ArrayBytes", "DoubledCapacity", "Push", "Probe", "Renamed", "AsDeclared"):
        assert label in covered, f"no exported use of {label}"
    # The library laws are about exactly the rules the modules now produce.
    libraries = LEAN / "Mettapedia/GSLT/LanguageDef/MacroExportLibraries.lean"
    source = libraries.read_text()
    embedded = {name: " ".join(body.split()) for name, body in
                re.findall(r"^def (\w+)Atom : Atom :=\n(.*?)\n\n", source, re.S | re.M)}
    for name, label, module, theory in LIBRARY_RULES:
        current = " ".join(atom(rules[(str(module), theory, label)]).split())
        assert embedded.get(name) == current, f"MacroExportLibraries.{name}Atom is not the rule {label} now reports"
    laws = WORK / "library-laws.lean"
    laws.write_text("import Mettapedia.GSLT.LanguageDef.MacroExportLibraries\n" + "".join(
        f"#print axioms Mettapedia.GSLT.LanguageDef.MacroExportLibraries.{law}\n" for law in LIBRARY_LAWS))
    axioms(kernel(laws, laws.read_text().splitlines(), options.lean_memory_mb))
    # The earlier lowering laws, with their axioms audited.
    lowering = LEAN / "Mettapedia/GSLT/LanguageDef/MacroExportLowering.lean"
    audit = WORK / "lowering-axioms.lean"
    audit.write_text(lowering.read_text() + "".join(
        f"\nopen Mettapedia.GSLT.LanguageDef.MacroExportLowering in\n#print axioms {name}\n"
        for name in ("export_block_eval", "call_trace_any_order", "call_order_observable", "prototype_converts")))
    laws = subprocess.run(lean_command(audit, options.lean_memory_mb), cwd=LEAN, capture_output=True)
    (WORK / "lowering.log").write_bytes(laws.stdout + laws.stderr)
    if laws.returncode or b"warning:" in laws.stdout or b"error" in laws.stdout:
        raise RuntimeError((laws.stdout + laws.stderr).decode(errors="replace")[-8000:])
    axioms(laws.stdout.decode())
    plans = b"".join((WORK / f"{name}.plan").read_bytes() for name in ("library", "controls"))
    (WORK / "summary.json").write_text(json.dumps({
        "uses": len(uses), "rules": len(rule_index), "mutations_refused": refused,
        "plans_sha256": hashlib.sha256(plans).hexdigest(),
        "proof_scope": "every recorded target is the lowering of its source (admitUse, decided by the kernel); "
                       "recorded_export_correct gives its behavior equal to the source use for all states, "
                       "caller code and replies; every unsequenced operand of each exported block is a simple read "
                       "(disciplined, decided), so the order a compiler picks is unobservable; hygiene of picked "
                       "names against the written arguments; library laws stated on the shipped rules; every "
                       "recorded rule is in the declared forms, for which every use and header definition is "
                       "lowered and admitted (export_complete, checkHeader_function_complete, "
                       "checkHeader_macro_complete)",
        "instances": report, "header_definitions": header_report,
        "semantic_refusal_controls": refusals,
        "header_mutations_refused": header_refused}, indent=2) + "\n")
    print(f"export admission: {len(uses)} recorded exports and {len(headers)} header definitions of "
          f"{len(rule_index)} rules kernel-admitted, every rule in the declared forms the completeness theorems "
          f"cover, {sum(refused.values())} mutated targets and "
          f"{sum(header_refused.values())} mutated header definitions refused, "
          f"{refusals['evaluated_macro_rules_refused']} evaluated macro forms and the conflicting compound "
          f"assignment refused, hygiene checked, the library laws are about the shipped rules, controls compiled and run with gcc and "
          f"clang", flush=True)


if __name__ == "__main__":
    main()
