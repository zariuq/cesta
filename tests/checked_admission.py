"""Kernel-check the actual permission projections emitted by checked-c/1.

This admits the permission model, not scalar computations or printed C. Native
interfaces remain conditional contracts. Mutations retain a valid plan syntax
and must be refused by the Lean checker.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import checked_core
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
MODEL = "Mettapedia.GSLT.LanguageDef.CheckedBytes"
MODULES = [MODEL, MODEL.replace("CheckedBytes", "CheckedBytePlans"),
           MODEL.replace("CheckedBytes", "CheckedByteSafety"),
           MODEL.replace("CheckedBytes", "CheckedByteModules"),
           MODEL.replace("CheckedBytes", "CheckedByteControls")]
AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def string(value):
    return json.dumps(str(value), ensure_ascii=False)


def list_of(values):
    return "[" + ", ".join(values) + "]"


def slot(value):
    spelling = str(value)
    assert spelling in {"unit", "bool", "u8", "u16", "u32", "u64", "usize", "read", "write", "own"}
    return "." + {"bool": "boolean"}.get(spelling, spelling)


def kind(value):
    assert value in ("read", "write", "own", "array")
    return "." + ("local" if value == "array" else str(value))


def natural(value):
    result = int(value)
    assert result >= 0 and str(result) == str(value)
    return str(result)


def signature(declaration):
    assert declaration[0] in ("function", "foreign") and len(declaration) == 7
    assert declaration[2][0] == "params" and declaration[3][0] == "returns"
    assert len(declaration[3]) == 3 and declaration[4][0] == "effects"
    source = int(declaration[3][2])
    assert source >= -1
    obligation = "none"
    if declaration[0] == "foreign":
        field = declaration[6]
        assert len(field) == 2 and field[0] == "obligation" and isinstance(field[1], Text)
        obligation = "some " + string(field[1].data.decode())
    allocator = declaration[5]
    assert allocator[0] == "allocator" and len(allocator) in (1, 3)
    brand = "none" if len(allocator) == 1 else "some (" + string(allocator[1]) + ", " + string(allocator[2]) + ")"
    return ("⟨" + string(declaration[1]) + ", " + list_of(slot(x) for x in declaration[2][1:])
            + ", " + slot(declaration[3][1]) + ", " + ("none" if source == -1 else "some " + str(source))
            + ", " + list_of(string(x) for x in declaration[4][1:]) + ", " + obligation + ", " + brand + "⟩")


def action(event):
    name = event[0]
    if name == "invoke":
        assert len(event) == 3 and isinstance(event[2], list)
        return "(.invoke " + string(event[1]) + " " + list_of(
            "(" + natural(p[0]) + ", " + kind(p[1]) + ")" for p in event[2]) + ")"
    assert len(event) == 4
    a, b, mode = natural(event[1]), natural(event[2]), kind(event[3])
    if name in ("array", "own"):
        assert b == "0" and mode == (".local" if name == "array" else ".own")
        return f"(.acquire {a} {mode})"
    if name in ("borrow", "copy"):
        return f"(.borrow {a} {b} {mode} {'true' if name == 'copy' else 'false'})"
    if name == "move":
        return f"(.move {a} {b} {mode})"
    if name in ("read", "inspect", "write"):
        assert b == "0" and mode == (".write" if name == "write" else ".read")
        return f"(.read {a} {'true' if name == 'write' else 'false'})"
    if name in ("end", "drop"):
        assert b == "0"
        return f"(.{'endView' if name == 'end' else 'drop'} {a} {mode})"
    if name in ("take", "own-result"):
        assert b == "0" and mode == ".own"
        return f"(.{'take' if name == 'take' else 'ownResult'} {a})"
    if name == "borrow-result":
        return f"(.borrowResult {a} {b} {mode})"
    raise AssertionError(("unknown permission action", event))


def body(events):
    if not events:
        return ".done"
    event, *rest = events
    name = event[0]
    if name in ("return", "break", "continue"):
        assert len(event) == 1 and not rest
        return {"return": ".ret", "break": ".breakLoop", "continue": ".continueLoop"}[name]
    if name == "branch":
        assert len(event) == 3 and event[1][0] == "then" and event[2][0] == "else"
        return "(.branch " + body(event[1][1:]) + " " + body(event[2][1:]) + " " + body(rest) + ")"
    if name == "loop":
        return "(.loop " + body(event[1:]) + " " + body(rest) + ")"
    return "(.step " + action(event) + " " + body(rest) + ")"


def module(record):
    assert record[0] == "CheckedPermissions" and isinstance(record[1], Text)
    assert record[1].data == b"cesta-permissions/1"
    assert record[2][0] == "module" and len(record[2]) == 2
    assert record[3][0] == "word-bits" and record[3][1] in ("32", "64")
    foreign, functions = [], []
    for declaration in record[4:]:
        sig = signature(declaration)
        if declaration[0] == "foreign":
            foreign.append(sig)
            continue
        checks = declaration[6]
        assert checks[0] == "checks"
        parameters, offset = [], 1
        while offset < len(checks) and checks[offset][0] == "param":
            event = checks[offset]
            assert len(event) == 4 and event[2] == "0"
            parameters.append("(" + natural(event[1]) + ", " + slot(event[3]) + ")")
            offset += 1
        functions.append("⟨" + sig + ", " + list_of(parameters) + ", " + body(checks[offset:]) + "⟩")
    return "⟨" + list_of(foreign) + ", " + list_of(functions) + "⟩"


def mutations(record):
    """Independent changes to permissions, control flow, origin and effects."""
    changes = []
    for index, declaration in enumerate(record[4:], 4):
        if "own" in declaration[2][1:] or declaration[3][1] == "own":
            forged = copy.deepcopy(record)
            forged[index][5] = [Sym("allocator")]
            changes.append(("missing-allocator", forged))
    for index, declaration in enumerate(record[4:], 4):
        if declaration[0] != "function":
            continue
        checks = declaration[6]
        prefix = 1
        while prefix < len(checks) and checks[prefix][0] == "param":
            prefix += 1
        if prefix > 1:
            forged = copy.deepcopy(record)
            forged[index][6][1][1] = Sym("99999")
            changes.append(("parameter", forged))
        forged = copy.deepcopy(record)
        forged[index][6].insert(prefix, [Sym("read"), Sym("99999"), Sym("0"), Sym("read")])
        changes.append(("absent-access", forged))
        def visit(events, path):
            for i, event in enumerate(events):
                if not isinstance(event, list) or not event:
                    continue
                name = event[0]
                if name in ("end", "drop"):
                    changed = copy.deepcopy(record)
                    cursor = changed[index][6]
                    for j in path:
                        cursor = cursor[j]
                    del cursor[i]
                    changes.append(("missing-" + str(name), changed))
                if name == "borrow-result":
                    changed = copy.deepcopy(record)
                    cursor = changed[index][6]
                    for j in path:
                        cursor = cursor[j]
                    cursor[i][2] = Sym("99999")
                    changes.append(("return-origin", changed))
                if name == "invoke":
                    changed = copy.deepcopy(record)
                    cursor = changed[index][6]
                    for j in path:
                        cursor = cursor[j]
                    cursor[i][1] = Sym("undeclared_native_operation")
                    changes.append(("unknown-call", changed))
                if name == "borrow":
                    for action_name, mode in (("write", "write"), ("drop", "array")):
                        changed = copy.deepcopy(record)
                        cursor = changed[index][6]
                        for j in path:
                            cursor = cursor[j]
                        cursor.insert(i + 1, [Sym(action_name), event[2], Sym("0"), Sym(mode)])
                        changes.append(("live-borrow-" + action_name, changed))
                if name in ("branch", "loop"):
                    if name == "branch":
                        visit(event[1], path + [i, 1])
                        visit(event[2], path + [i, 2])
                    else:
                        visit(event, path + [i])
        visit(checks, [])
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    parser.add_argument("--module", action="append", type=Path, default=[])
    args = parser.parse_args()
    project = args.lean_project.resolve()
    work = Path(tempfile.mkdtemp(prefix="checked-admission-", dir=ROOT / "build"))
    extra = work / "Extra.cesta"
    extra.write_text(checked_core.MORE)
    sources = [ROOT / "checked/examples" / name for name in
               ("ByteViews.cesta", "Owners.cesta", "PackedWords.cesta", "FormatBytes.cesta", "ClientPipeline.cesta")]
    sources += [extra, *args.module]
    model_hashes = {}
    for name in MODULES:
        path = name.replace(".", "/")
        source, artifact = project / (path + ".lean"), project / ".lake/build/lib/lean" / (path + ".olean")
        if not artifact.exists() or artifact.stat().st_mtime_ns < source.stat().st_mtime_ns:
            raise RuntimeError("build the current permission model before admission: " + name)
        model_hashes[name] = {"source": hashlib.sha256(source.read_bytes()).hexdigest(),
                              "object": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    lines = [*("import " + name for name in MODULES), "set_option autoImplicit false",
             "set_option Elab.async false", "set_option maxRecDepth 16384",
             "set_option maxHeartbeats 8000000", "open " + MODEL]
    positive, negative = 0, 0
    for source in sources:
        result = checked_core.run([BINARY, "checked", "plan", source])
        (work / (source.stem + ".plan")).write_bytes(result.stdout)
        records = parse(result.stdout)
        assert records
        for record in records:
            lines += [f"def checked{positive} : Module := " + module(record),
                      f"theorem accepted{positive} : admit checked{positive} = true := by decide",
                      f"#print axioms accepted{positive}"]
            for label, mutated in mutations(record):
                lines += [f"def changed{negative} : Module := " + module(mutated),
                          f"theorem refused{negative} : admit changed{negative} = false := by decide",
                          f"#print axioms refused{negative}"]
                negative += 1
            positive += 1
    path = work / "Admission.lean"
    path.write_text("\n\n".join(lines) + "\n")
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb:
        command += ["-M", str(args.lean_memory_mb)]
    result = subprocess.run([*command, path], cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (work / "lean.log").write_bytes(result.stdout)
    if result.returncode:
        raise AssertionError((path, result.stdout.decode(errors="replace")[-9000:]))
    for item in re.findall(r"depends on axioms: \[(.*?)\]", result.stdout.decode(), re.S):
        used = {word.strip() for word in item.split(",") if word.strip()}
        assert used <= AXIOMS, used
    for name, before in model_hashes.items():
        path = name.replace(".", "/")
        assert hashlib.sha256((project / (path + ".lean")).read_bytes()).hexdigest() == before["source"]
        assert hashlib.sha256((project / ".lake/build/lib/lean" / (path + ".olean")).read_bytes()).hexdigest() == before["object"]
    summary = {"binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
               "models": model_hashes, "admitted_modules": positive, "refused_mutations": negative,
               "work": str(work), "boundary": "permission projection; native contracts remain conditional"}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "build/checked-admission-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"checked admission: {positive} actual modules; {negative} refused permission mutations")


if __name__ == "__main__":
    main()
