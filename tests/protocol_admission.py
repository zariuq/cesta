"""Kernel-admit actual source/target protocol plans and refuse changed targets."""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from protocols import CESTA, ROOT, declaration, module, mutations, run
from oracles.pro_compact_cesta import Sym, parse

MODEL = "Mettapedia.GSLT.LanguageDef.ExecutableProtocol"
MODULES = [MODEL, MODEL + "Safety", MODEL + "Controls", MODEL + "Composition"]
INSTRUMENTATION_MODULES = [MODEL + "Instrumentation", MODEL + "InstrumentationCorrectness",
                           MODEL + "InstrumentationControls"]
MODULES += INSTRUMENTATION_MODULES


def string(s):
    return json.dumps(str(s), ensure_ascii=False)


def strings(values):
    return "[" + ", ".join(string(s) for s in values) + "]"


def body(value, target=False):
    kind = str(value[0])
    constructors = ({"Return": "ret", "IfCall": "ifCall", "VoidCall": "voidCall"} if target else
                    {"Return": "ret", "Call": "call", "Acquire": "acquire", "Release": "release", "Transfer": "transfer"})
    first = "." + str(value[1]) if kind == "Return" else string(value[1])
    return "(." + constructors[kind] + " " + first + "".join(" " + body(x, target) for x in value[2:]) + ")"


def source(name, data):
    assert data[0] == 'Protocol' and len(data) in (7, 8)
    if len(data) == 8:
        assert data[7][0] == 'Checks'
    hooks = ["⟨" + ", ".join([string(h[1]), string(h[2]), "." + str(h[3]),
                              strings(h[4][1:]), strings(h[5][1:]), strings(h[6][1:])]) + "⟩"
             for h in data[3][1:]]
    resources = []
    for r in data[4][1:]:
        # Match the native syntax exactly; a future clause must not disappear
        # into a default-empty prerequisite list in the independent model.
        assert len(r) in (5, 7) and r[0] == "Resource", r
        if len(r) == 7:
            assert r[5][0] == "AcquireRequires" and r[6][0] == "TransferRequires", r
        resources.append("⟨" + ", ".join([*(string(x) for x in r[1:5]),
                         strings(r[5][1:] if len(r) == 7 else []),
                         strings(r[6][1:] if len(r) == 7 else [])]) + "⟩")
    return "⟨" + ", ".join([string(name), string(data[2][1]), "[" + ", ".join(hooks) + "]",
                             "[" + ", ".join(resources) + "]", strings(data[5][1][1:]),
                             strings(data[5][2][1:]), body(data[6][1])]) + "⟩"


def checks(data):
    values = data[7][1:] if len(data) == 8 else []
    return '[' + ', '.join('⟨.' + {'Fact': 'fact', 'Own': 'own'}[v[0]] + ', ' +
                           string(v[1]) + ', ' + string(v[2]) + '⟩' for v in values) + ']'


def reason(value):
    if value == [Sym('entry')]:
        return '.entry'
    assert len(value) == 3 and value[0] == 'afterBool' and value[2] in ('true', 'false')
    return '(.afterBool ' + string(value[1]) + ' ' + str(value[2]) + ')'


def instrumented(value):
    kind = value[0]
    if kind == 'Validate':
        assert len(value) == 6 and value[1] in ('fact', 'own') and value[4] in ('true', 'false')
        return '(.validate ⟨.' + str(value[1]) + ', ' + string(value[2]) + ', ' + string(value[3]) + ', ' + str(value[4]) + '⟩ ' + instrumented(value[5]) + ')'
    if kind == 'Tick':
        assert len(value) == 3
        return '(.tick ' + reason(value[1]) + ' ' + instrumented(value[2]) + ')'
    first = '.' + str(value[1]) if kind == 'Return' else string(value[1])
    return '(.' + {'Return': 'ret', 'IfCall': 'ifCall', 'VoidCall': 'voidCall'}[kind] + ' ' + first + ''.join(' ' + instrumented(child) for child in value[2:]) + ')'


def corrupt_instrumentation(tree):
    changes = []
    def change(path, edit):
        result = copy.deepcopy(tree)
        if not path:
            return edit(result)
        cursor = result
        for index in path[:-1]:
            cursor = cursor[index]
        cursor[path[-1]] = edit(cursor[path[-1]])
        return result
    def visit(value, path):
        kind = value[0]
        if kind == 'Validate':
            changes.append(change(path, lambda v: v[5]))
            def flip(v):
                v[4] = Sym('false' if v[4] == 'true' else 'true'); return v
            changes.append(change(path, flip))
            def forge(v):
                v[3] = Sym('forged_validator'); return v
            changes.append(change(path, forge))
            visit(value[5], path + [5])
        elif kind == 'Tick':
            changes.append(change(path, lambda v: v[2]))
            changes.append(change(path, lambda v: [Sym('Tick'), copy.deepcopy(v[1]), v]))
            def wrong_reason(v):
                v[1] = [Sym('entry')] if v[1][0] != 'entry' else [Sym('afterBool'), Sym('forged_guard'), Sym('false')]
                return v
            changes.append(change(path, wrong_reason))
            visit(value[2], path + [2])
        elif kind == 'IfCall':
            if value[2] != value[3]:
                changes.append(change(path, lambda v: [v[0], v[1], v[3], v[2]]))
            visit(value[2], path + [2]); visit(value[3], path + [3])
        elif kind == 'VoidCall':
            visit(value[2], path + [2])
    visit(tree, [])
    return changes


def corrupt_targets(tree):
    """Change each call or return occurrence, preserving a well-typed C tree."""
    changes = []
    def visit(value, path):
        changed = copy.deepcopy(tree)
        cursor = changed
        for index in path:
            cursor = cursor[index]
        if value[0] == "Return":
            cursor[1] = Sym("done" if value[1] != "done" else "raised")
        else:
            cursor[1] = Sym("forged_native_call")
        changes.append(changed)
        if value[0] == "IfCall" and value[2] != value[3]:
            swapped = copy.deepcopy(tree)
            cursor = swapped
            for index in path:
                cursor = cursor[index]
            cursor[2], cursor[3] = cursor[3], cursor[2]
            changes.append(swapped)
        for index in range(2, len(value)):
            visit(value[index], path + [index])
    visit(tree, [])
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    parser.add_argument("--module", action="append", type=Path, default=[])
    args = parser.parse_args()
    project = args.lean_project.resolve()
    work = Path(tempfile.mkdtemp(prefix="protocol-admission-", dir=ROOT / "build"))
    binary_hash = hashlib.sha256(CESTA.read_bytes()).hexdigest()
    models = {}
    for name in MODULES:
        path = name.replace(".", "/")
        src, obj = project / (path + ".lean"), project / ".lake/build/lib/lean" / (path + ".olean")
        if not obj.exists() or obj.stat().st_mtime_ns < src.stat().st_mtime_ns:
            raise RuntimeError("build the current protocol model before admission: " + name)
        models[name] = {"source": hashlib.sha256(src.read_bytes()).hexdigest(), "object": hashlib.sha256(obj.read_bytes()).hexdigest()}
    lines = [*("import " + name for name in MODULES), "set_option autoImplicit false",
             "set_option Elab.async false", "set_option maxRecDepth 8192",
             "set_option maxHeartbeats 8000000", "open " + MODEL,
             # Admit the real lowering once. For each corrupted output, decide
             # its difference and use the checker law rather than repeating
             # identical source/declaration checks hundreds of times.
             "theorem rejectChangedInstrumentation {plan : Instrumentation.RecordedPlan}",
             "    (valid : Instrumentation.PlanValid plan) (target : Instrumentation.IStmt)",
             "    (different : target ≠ plan.target) :",
             "    Instrumentation.checkPlan ⟨plan.protocol, plan.checks, target⟩ = false := by",
             "  have unequal : plan.target ≠ target := Ne.symm different",
             "  simp [Instrumentation.checkPlan, valid.target, unequal]",
             "#print axioms rejectChangedInstrumentation"]
    admitted, refused, instrumented_admitted, instrumented_refused = 0, 0, 0, 0
    for path in [ROOT / "protocols/Admission.module", ROOT / "protocols/Delivery.module", *args.module]:
        name, data = declaration(path)
        emitted = run([CESTA, "protocol", "plan", path, "Protocols"]).stdout
        (work / f"plan-{admitted}.sexp").write_text(emitted)
        record = parse(emitted.encode())[0]
        assert record[1] == name and record[2:7] == data[2:7]
        target = record[7][1]
        lines += [f"def source{admitted} : Protocol := " + source(name, data),
                  f"def target{admitted} : CStmt := " + body(target, True),
                  f"def plan{admitted} : Plan := ⟨source{admitted}, target{admitted}⟩",
                  f"theorem accepted{admitted} : checkPlan plan{admitted} = true := by decide",
                  f"theorem exact{admitted} {{World : Type}} (host : Host World) (world : World) (replies : List Bool) :",
                  f"  evalC host target{admitted} world replies = evalSource source{admitted} host source{admitted}.body world replies :=",
                  f"  ((checkPlan_iff _).mp accepted{admitted}).eval_exact host world replies",
                  f"#print axioms exact{admitted}"]
        for changed in corrupt_targets(target):
            lines += [f"example : checkPlan ⟨source{admitted}, " + body(changed, True) + "⟩ = false := by decide"]
            refused += 1
        section = record[8]
        assert section[0] == 'Instrumentation'
        assert section[1] == (data[7] if len(data) == 8 else [Sym('Checks')])
        assert section[2][0] == 'Target', 'shipped examples require complete validators'
        itarget = section[2][1]
        lines += [f'def checks{admitted} : List Instrumentation.Validator := ' + checks(data),
                  f'def itarget{admitted} : Instrumentation.IStmt := ' + instrumented(itarget),
                  f'def iplan{admitted} : Instrumentation.RecordedPlan := ⟨source{admitted}, checks{admitted}, itarget{admitted}⟩',
                  f'theorem iaccepted{admitted} : Instrumentation.checkPlan iplan{admitted} = true := by decide',
                  f'theorem ivalid{admitted} : Instrumentation.PlanValid iplan{admitted} := (Instrumentation.checkPlan_iff _).mp iaccepted{admitted}',
                  f'#print axioms ivalid{admitted}',
                  f'theorem instrumentedExact{admitted} {{World : Type}} (mode : Instrumentation.Mode) (host : Host World)',
                  '    (validator : String → World → Bool) (world : World) (replies : List Bool)',
                  '    (counters : Instrumentation.CounterStore) (status : Status)',
                  f'    (normal : (Instrumentation.eval mode host validator itarget{admitted} world replies counters).status = .core status) :',
                  f'    (Instrumentation.eval mode host validator itarget{admitted} world replies counters).project =',
                  f'      some (evalSource source{admitted} host source{admitted}.body world replies) :=',
                  f'  ivalid{admitted}.normal_eval_exact mode host validator world replies counters status normal',
                  f'#print axioms instrumentedExact{admitted}',
                  f'theorem disabledExact{admitted} {{World : Type}} (host : Host World)',
                  '    (validator : String → World → Bool) (world : World) (replies : List Bool)',
                  '    (counters : Instrumentation.CounterStore) :',
                  f'    Instrumentation.eval ⟨false, false⟩ host validator itarget{admitted} world replies counters =',
                  f'      Instrumentation.liftCore (evalSource source{admitted} host source{admitted}.body world replies) counters :=',
                  f'  ivalid{admitted}.disabled_eval_exact host validator world replies counters',
                  f'#print axioms disabledExact{admitted}',
                  f'theorem declineReason{admitted} {{World : Type}} (mode : Instrumentation.Mode) (host : Host World)',
                  '    (validator : String → World → Bool) (world : World) (replies : List Bool)',
                  '    (counters : Instrumentation.CounterStore) (counted : mode.counted = true)',
                  f'    (returned : (Instrumentation.eval mode host validator itarget{admitted} world replies counters).status = .core (.returned .declined)) :',
                  f'    Instrumentation.declineKeys (Instrumentation.eval mode host validator itarget{admitted} world replies counters).observations =',
                  f'      [Instrumentation.lastDecision .entry (Instrumentation.coreCalls (Instrumentation.eval mode host validator itarget{admitted} world replies counters).observations)] :=',
                  f'  ivalid{admitted}.decline_reason mode host validator world replies counters counted returned',
                  f'#print axioms declineReason{admitted}',
                  f'example : itarget{admitted}.erase = target{admitted} := by decide']
        instrumented_admitted += 1
        for changed in corrupt_instrumentation(itarget):
            lines += [f'example : Instrumentation.checkPlan ⟨source{admitted}, checks{admitted}, ' + instrumented(changed) + '⟩ = false :=',
                      f'  rejectChangedInstrumentation ivalid{admitted} _ (by decide)']
            instrumented_refused += 1
        admitted += 1
    name, data = declaration(ROOT / "protocols/Admission.module")
    for label, changed in mutations(data):
        # C lexical adequacy is a stronger native check, not a Lean claim.
        if label in ("C keyword", "parameter shadow", "wrong arity", "unknown opcode", "invalid outcome", "unknown kind"):
            continue
        lines += ["example : check (" + source(name, changed) + ") = false := by decide"]
        refused += 1
    file = work / "ActualProtocols.lean"
    file.write_text("\n".join(lines) + "\n")
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb:
        command += ["-M", str(args.lean_memory_mb)]
    result = subprocess.run([*command, str(file)], cwd=project, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (work / "lean.log").write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(result.stdout)
    for names in re.findall(r"depends on axioms:\s*\[(.*?)\]", result.stdout, re.S):
        assert set(x.strip() for x in names.split(",") if x.strip()) <= {"propext", "Classical.choice", "Quot.sound"}
    assert hashlib.sha256(CESTA.read_bytes()).hexdigest() == binary_hash
    for name, recorded in models.items():
        path = name.replace(".", "/")
        assert hashlib.sha256((project / (path + ".lean")).read_bytes()).hexdigest() == recorded["source"]
        assert hashlib.sha256((project / ".lake/build/lib/lean" / (path + ".olean")).read_bytes()).hexdigest() == recorded["object"]
    (work / "summary.json").write_text(json.dumps(dict(binary_sha256=binary_hash, models=models,
         admitted=admitted, refused=refused, instrumented_admitted=instrumented_admitted,
         instrumented_refused=instrumented_refused,
         boundary="Native hook contracts, validator purity, private counter storage and C printing/compiler behavior remain tested obligations."), indent=2) + "\n")
    print(f"protocol admission: {admitted} actual plans, {refused} refused mutations; {work.name}")
    print(f'instrumentation admission: {instrumented_admitted} actual plans, {instrumented_refused} refused mutations')


if __name__ == "__main__":
    main()
