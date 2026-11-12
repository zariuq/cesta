"""Independent Lean admission of actual typed span permission plans."""

import argparse
import copy
from pathlib import Path
import subprocess
import tempfile

import checked_admission as legacy
import checked_spans
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = checked_spans.ROOT
MODEL = "Mettapedia.GSLT.LanguageDef.CheckedScalarSpans"


def element_type(value):
    if isinstance(value, list):
        assert len(value) == 3 and value[0] == "span"
        assert value[1] in checked_spans.ELEMENTS and value[2] in ("read", "write", "own", "array")
        return str(value[1]), str(value[2])
    if value in ("read", "write", "own", "array"):
        return "u8", str(value)
    assert value in ("unit", *checked_spans.ELEMENTS)
    return None, str(value)


def element(value):
    return "." + ("boolean" if value == "bool" else value)


def slot(value):
    e, mode = element_type(value)
    return f"(.span {element(e)} {legacy.kind(mode)})" if e else f"(.scalar {legacy.slot(mode)})"


def signature(declaration):
    assert declaration[0] in ("function", "foreign") and len(declaration) == 7
    assert declaration[2][0] == "params" and declaration[3][0] == "returns"
    assert declaration[4][0] == "effects" and declaration[5][0] == "allocator"
    source = int(declaration[3][2]); assert source >= -1
    alloc = declaration[5]
    assert len(alloc) in (1, 3)
    family = "none" if len(alloc) == 1 else "some (" + legacy.string(alloc[1]) + ", " + legacy.string(alloc[2]) + ")"
    obligation = "none"
    if declaration[0] == "foreign":
        assert declaration[6][0] == "obligation" and isinstance(declaration[6][1], Text)
        obligation = "some " + legacy.string(declaration[6][1].data.decode())
    return ("⟨" + legacy.string(declaration[1]) + ", " + legacy.list_of(map(slot, declaration[2][1:])) + ", "
            + slot(declaration[3][1]) + ", " + ("none" if source == -1 else f"some {source}")
            + ", " + legacy.list_of(map(legacy.string, declaration[4][1:]))
            + ", " + obligation + ", " + family + "⟩")


def action(event):
    normalized = copy.deepcopy(event)
    if event[0] == "invoke":
        elements = [element(element_type(arg[1])[0]) for arg in event[2]]
        for argument in normalized[2]:
            argument[1] = Sym(element_type(argument[1])[1])
        return "⟨" + legacy.action(normalized) + ", none, " + legacy.list_of(elements) + "⟩"
    e, mode = element_type(event[3]); assert e
    normalized[3] = Sym(mode)
    return "⟨" + legacy.action(normalized) + ", some " + element(e) + ", []⟩"


def body(events):
    if not events: return ".done"
    event, *rest = events
    if event[0] in ("return", "break", "continue"):
        assert len(event) == 1 and not rest
        return {"return": ".ret", "break": ".breakLoop", "continue": ".continueLoop"}[event[0]]
    if event[0] == "branch":
        assert len(event) == 3 and event[1][0] == "then" and event[2][0] == "else"
        return f"(.branch {body(event[1][1:])} {body(event[2][1:])} {body(rest)})"
    if event[0] == "loop": return f"(.loop {body(event[1:])} {body(rest)})"
    return f"(.step {action(event)} {body(rest)})"


def module(record):
    assert record[0] == "CheckedPermissions" and record[1].data in (b"cesta-permissions/1", b"cesta-permissions/2")
    assert record[2][0] == "module" and record[3][0] == "word-bits" and record[3][1] in ("32", "64")
    foreign, functions = [], []
    for declaration in record[4:]:
        sig = signature(declaration)
        if declaration[0] == "foreign": foreign.append(sig); continue
        assert declaration[6][0] == "checks"
        events = declaration[6][1:]; parameters = []
        while events and events[0][0] == "param":
            event, *events = events
            assert len(event) == 4 and event[2] == "0"
            parameters.append(f"({legacy.natural(event[1])}, {slot(event[3])})")
        functions.append("⟨" + sig + ", " + legacy.list_of(parameters) + ", " + body(events) + "⟩")
    return "⟨" + legacy.list_of(foreign) + ", " + legacy.list_of(functions) + "⟩"


def mutations(record):
    def walk(node, path=()):
        if not isinstance(node, list): return
        if node and node[0] in ("read", "write", "borrow", "move", "take", "own-result", "borrow-result") and len(node) == 4:
            yield path
        for i, child in enumerate(node):
            yield from walk(child, path + (i,))
    for path in walk(record):
        changed = copy.deepcopy(record); event = changed
        for i in path: event = event[i]
        e, mode = element_type(event[3])
        event[3] = [Sym("span"), Sym("u16" if e != "u16" else "u32"), Sym(mode)]
        yield changed
    def calls(node, path=()):
        if not isinstance(node, list): return
        if node and node[0] == "invoke":
            for i in range(len(node[2])): yield path + (2, i, 1)
        for i, child in enumerate(node): yield from calls(child, path + (i,))
    for path in calls(record):
        changed = copy.deepcopy(record); node = changed
        for i in path[:-1]: node = node[i]
        e, mode = element_type(node[path[-1]])
        node[path[-1]] = [Sym("span"), Sym("u16" if e != "u16" else "u32"), Sym(mode)]
        yield changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="span-admission-", dir=ROOT / "build"))
    source = work / "spans.cesta"; source.write_text(checked_spans.document(checked_spans.functions()))
    lines = ["import " + MODEL, "set_option autoImplicit false", "set_option Elab.async false",
             "set_option maxRecDepth 32768", "set_option maxHeartbeats 16000000", "open " + MODEL]
    positives = negatives = 0
    for source in [source, ROOT / "checked/examples/FlatFire.cesta", ROOT / "checked/examples/CopySpan.cesta",
                   ROOT / "adapters/tisa/examples/MixedCopy.cesta",
                   ROOT / "checked/examples/ByteViews.cesta", ROOT / "checked/examples/Owners.cesta"]:
        raw = checked_spans.run([checked_spans.BINARY, "checked", "plan", source]).stdout
        (work / (source.stem + ".plan")).write_bytes(raw)
        for record in parse(raw):
            lines += [f"def actual{positives} : Module := " + module(record),
                      f"theorem accepted{positives} : admit actual{positives} = true := by decide",
                      f"#print axioms accepted{positives}"]
            # Mutate each function independently while retaining the full signature table.
            for changed in mutations(record):
                lines += [f"example : admit ({module(changed)}) = false := by decide"]
                negatives += 1
            positives += 1
    for theorem in ("transition_valid", "transition_erases", "checked_no_fault", "allocationBytes_exact", "slice_byte_bounds"):
        lines.append("#print axioms " + theorem)
    proof = work / "Admission.lean"; proof.write_text("\n\n".join(lines)+"\n")
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb is not None: command += ["-M", str(args.lean_memory_mb)]
    p = subprocess.run([*command, proof],
                       cwd=args.lean_project, capture_output=True)
    (work / "lean.log").write_bytes(p.stdout + p.stderr)
    assert p.returncode == 0, (work, p.stdout.decode()[-12000:], p.stderr.decode())
    assert b"sorryAx" not in p.stdout
    print(f"scalar span admission: {positives} actual modules; {negatives} element mutations refused; old byte plans accepted")


if __name__ == "__main__":
    main()
