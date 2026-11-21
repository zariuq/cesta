"""Kernel-admit the ownership plans emitted from the actual typed C bodies.

The permission/control model is proved. C parsing, projection, native contracts
and compiler fidelity remain distinct tested or conditional boundaries.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
import subprocess
import sys
import threading
sys.setrecursionlimit(1000000)   # scratch only: plans of long functions nest deeply

import ownership_core
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = Path(__file__).resolve().parent.parent
MODEL = "Mettapedia.Ownership"


def run(command, cwd=ROOT):
    result = subprocess.run(list(map(str,command)),cwd=cwd,capture_output=True,text=True)
    if result.returncode:
        raise AssertionError((command,result.returncode,result.stdout,result.stderr))
    return result


def natural(value):
    number = int(str(value))
    assert number >= 0 and str(number) == str(value),value
    return str(number)


def region(values):
    assert len(values) == 3
    return "⟨"+", ".join(natural(v) for v in values)+"⟩"


def mode(value):
    assert value in ("read","write"),value
    return ".exclusive" if value == "write" else ".shared"


def boolean(value):
    assert value in ("read","write","nullable","nonnull"),value
    return "true" if value in ("write","nullable") else "false"


def action(event):
    name = str(event[0])
    if name == "whole-root":
        assert len(event) == 3
        # A logical whole-span unit, interpreted by OwnershipViewExtents at
        # every caller byte extent. This is not an allocation of one byte.
        return f"(.root {natural(event[1])} 1 {mode(event[2])} .input 0)"
    if name == "whole-view":
        assert len(event) == 4
        return f"(.view {natural(event[1])} ⟨{natural(event[2])}, 0, 1⟩ 1 {boolean(event[3])})"
    if name == "root":
        assert len(event) == 6 and event[4] in ("local","input","heap","static")
        return f"(.root {natural(event[1])} {natural(event[2])} {mode(event[3])} .{event[4]} {natural(event[5])})"
    if name == "borrow":
        assert len(event) == 7
        return f"(.borrow {natural(event[1])} {natural(event[2])} {region(event[3:6])} {mode(event[6])})"
    if name in ("access","result"):
        assert len(event) == 6
        return f"(.{name} {natural(event[1])} {region(event[2:5])} {boolean(event[5])})"
    if name in ("field-result", "record-result"):
        assert len(event) == 7
        constructor = "fieldResult" if name == "field-result" else "recordResult"
        return f"(.{constructor} {natural(event[1])} {natural(event[2])} {region(event[3:6])} {boolean(event[6])})"
    if name in ("null-field-result", "null-record-result"):
        assert len(event) == 2
        constructor = "nullFieldResult" if name == "null-field-result" else "nullRecordResult"
        return f"(.{constructor} {natural(event[1])})"
    if name == "record-give":
        assert len(event) == 4
        return f"(.recordGive {natural(event[1])} {natural(event[2])} {natural(event[3])})"
    if name in ("vacate", "null-owner"):
        assert len(event) == 3
        constructor = "nullOwner" if name == "null-owner" else name
        return f"(.{constructor} {natural(event[1])} {natural(event[2])})"
    if name == "restore":
        assert len(event) == 2
        return f"(.restore {natural(event[1])})"
    if name == "replace":
        assert len(event) == 4
        return f"(.replace {natural(event[1])} {natural(event[2])} {natural(event[3])})"
    if name == "view":
        assert len(event) == 7
        return f"(.view {natural(event[1])} {region(event[2:5])} {natural(event[5])} {boolean(event[6])})"
    if name == "restrict":
        assert len(event) == 5
        return f"(.restrict {natural(event[1])} {region(event[2:5])})"
    if name == "end":
        assert len(event) == 2
        return f"(.endLoan {natural(event[1])})"
    if name in ("move","release","give","handoff"):
        assert len(event) == 3
        return f"(.{name} {natural(event[1])} {natural(event[2])})"
    if name == "null-result":
        assert len(event) == 1
        return ".nullResult"
    if name == "invoke":
        assert len(event) == 3 and event[2][0] == "arguments"
        arguments = []
        for arg in event[2][1:]:
            assert arg[0] in ("value", "loan", "maybe", "owned", "released", "null")
            if arg[0] in ("value", "null"):
                assert len(arg) == 1
                arguments.append("."+str(arg[0]))
            else:
                assert len(arg) == 2
                arguments.append("(."+str(arg[0])+" "+natural(arg[1])+")")
        return "(.invoke "+json.dumps(str(event[1]))+" ["+", ".join(arguments)+"])"
    raise AssertionError(("unknown ownership event",event))


def body(events):
    if not events:
        return ".done"
    event, *rest = events
    name = str(event[0])
    if name in ("return","continue","break","arm-exit"):
        assert len(event) == 1 and not rest,(name,rest)
        return {"return":".ret","continue":".continueLoop","break":".breakLoop","arm-exit":".done"}[name]
    if name == "branch":
        assert len(event) == 3 and event[1][0] == "then" and event[2][0] == "else"
        return "(.branch "+body(event[1][1:])+" "+body(event[2][1:])+" "+body(rest)+")"
    if name == "loop":
        return "(.loop "+body(event[1:])+" "+body(rest)+")"
    if name in ("once", "iteration"):
        assert len(event) == 3 and event[1][0] == "first" and event[2][0] == "next"
        normal = body(event[2][1:]+rest)
        broken = body(rest) if name == "once" else ".breakLoop"
        return "(Mettapedia.Machines.CMemory.PermissionFlow.resumeIteration "+body(event[1][1:])+" "+normal+" "+broken+")"
    if name == "switch":
        # Conditions are erased to nondeterminism, including no matching arm.
        # Each arm owns its break; outer loop transfers remain distinct.
        choice = ".done"
        for arm in reversed(event[1:]):
            assert arm[0] == "arm"
            choice = "(.branch "+body(arm[1:])+" "+choice+" .done)"
        return "(.branch "+choice+" .done "+body(rest)+")"
    if name == "switch-total":
        arms = event[1:]
        assert arms and all(arm[0] == "arm" for arm in arms)
        choice = body(arms[-1][1:])
        if len(arms) == 1:
            return "(.branch "+choice+" "+choice+" "+body(rest)+")"
        for arm in reversed(arms[1:-1]):
            choice = "(.branch "+body(arm[1:])+" "+choice+" .done)"
        return "(.branch "+body(arms[0][1:])+" "+choice+" "+body(rest)+")"
    return "(.step "+action(event)+" "+body(rest)+")"


def result_spec(declaration):
    assert declaration[0] == "returns"
    kind = str(declaration[1])
    if kind == "value":
        assert len(declaration) == 2
        return ".value"
    if kind == "static":
        assert len(declaration) == 3
        return "(.static "+boolean(declaration[2])+")"
    if kind == "own":
        assert len(declaration) == 4
        return "(.owned "+natural(declaration[2])+" "+boolean(declaration[3])+")"
    if kind == "view":
        assert len(declaration) == 6
        return "(.viewed "+region(declaration[2:5])+" "+boolean(declaration[5])+")"
    assert kind == "borrow" and len(declaration) == 7
    return "(.borrowed "+region(declaration[2:5])+" "+boolean(declaration[5])+" "+boolean(declaration[6])+")"


def signature(declaration):
    assert declaration[0] == "signature" and len(declaration) in (4, 5, 6)
    parameters, variadic, result = declaration[1:4]
    assert parameters[0] == "parameters" and variadic[0] == "variadic"
    ps = []
    for p in parameters[1:]:
        if p[0] == "value":
            assert len(p) == 1
            ps.append(".value")
        elif p[0] in ("borrowed", "optional-borrowed"):
            assert len(p) == 3
            ps.append("(."+("optionalBorrowed" if p[0] == "optional-borrowed" else "borrowed")+" "+natural(p[1])+" "+boolean(p[2])+")")
        elif p[0] in ("owned", "optional-owned"):
            assert len(p) == 3
            ps.append("(."+("optionalOwned" if p[0] == "optional-owned" else "owned")+" "+natural(p[1])+" "+natural(p[2])+")")
        elif p[0] == "static":
            assert len(p) == 2
            ps.append("(.static "+boolean(p[1])+")")
        elif p[0] == "viewed":
            assert len(p) == 2
            ps.append("(.viewed "+boolean(p[1])+")")
        elif p[0] == "optional-viewed":
            assert len(p) == 2
            ps.append("(.optionalViewed "+boolean(p[1])+")")
        else:
            assert p[0] in ("released", "resized") and len(p) == 2
            ps.append("(."+str(p[0])+" "+natural(p[1])+")")
    assert len(variadic) == 2 and variadic[1] in ("read", "none")
    assert result[0] == "result"
    if result[1] == "whole-viewed":
        assert len(result) == 4
        returns = "(.wholeViewed "+natural(result[2])+" "+boolean(result[3])+")"
    elif result[1] == "viewed":
        assert len(result) == 6
        returns = "(.viewed "+" ".join(natural(n) for n in result[2:5])+" "+boolean(result[5])+")"
    elif result[1] == "borrowed":
        assert len(result) == 7
        returns = "(.borrowed "+" ".join(natural(n) for n in result[2:5])+" "+boolean(result[5])+" "+boolean(result[6])+")"
    else:
        returns = result_spec([Sym("returns"), Sym("own") if result[1] == "owned" else result[1], *result[2:]])
    fields, records = [], []
    for section, expected, output in zip(declaration[4:], ("fields", "records"), (fields, records)):
        assert section[0] == expected
        for field in section[1:]:
            assert len(field) == 3 and field[0] == "field"
            shape = field[2]
            if shape[0] == "static":
                assert len(shape) == 2
                code = "(.static "+boolean(shape[1])+")"
            elif shape[0] == "owned":
                assert expected == "records" and len(shape) == 3
                code = "(.owned "+natural(shape[1])+" "+boolean(shape[2])+")"
            elif shape[0] == "whole-viewed":
                assert len(shape) == 3
                code = "(.wholeViewed "+natural(shape[1])+" "+boolean(shape[2])+")"
            else:
                assert shape[0] == "borrowed" and len(shape) == 6
                code = "(.borrowed "+" ".join(natural(n) for n in shape[1:4])+" "+boolean(shape[4])+" "+boolean(shape[5])+")"
            output.append("("+natural(field[1])+", "+code+")")
    return "⟨["+", ".join(ps)+"], "+("true" if variadic[1] == "read" else "false")+", "+returns+", ["+", ".join(fields)+"], ["+", ".join(records)+"]⟩"


def function(declaration):
    assert declaration[0] == "function" and len(declaration) == 7 and declaration[6][0] == "body"
    assert declaration[3][0] == "inputs" and declaration[4][0] == "ambient"
    inputs = "["+", ".join(natural(n) for n in declaration[3][1:])+"]"
    ambient = "["+", ".join("("+natural(p[0])+", "+natural(p[1])+")" for p in declaration[4][1:])+"]"
    return "⟨"+json.dumps(str(declaration[1]))+", "+signature(declaration[2])+", "+inputs+", "+ambient+", "+result_spec(declaration[5])+", "+body(declaration[6][1:])+"⟩"


def module_parts(record):
    assert record[0] == "Ownership" and isinstance(record[1],Text) and record[1].data in (
        b"cesta-ownership/2", b"cesta-ownership/3", b"cesta-ownership/4", b"cesta-ownership/5", b"cesta-ownership/6", b"cesta-ownership/7")
    if record[1].data not in (b"cesta-ownership/4", b"cesta-ownership/5", b"cesta-ownership/6", b"cesta-ownership/7"):
        def old_syntax(item):
            if isinstance(item, list):
                assert not item or item[0] not in ("whole-root", "whole-view", "whole-viewed")
                for child in item:
                    old_syntax(child)
        old_syntax(record)
    foreign, functions = [], []
    abi = False
    callbacks = False
    interfaces = {}
    implementations = []
    for item in record[2:]:
        if item[0] == "abi":
            assert not abi and record[1].data in (b"cesta-ownership/3", b"cesta-ownership/4", b"cesta-ownership/5", b"cesta-ownership/6", b"cesta-ownership/7")
            assert len(item) == 4 and isinstance(item[1],Text) and item[1].data == b"cesta-abi/2"
            assert len(item[2]) == 2 and item[2][0] == 'sha256' and isinstance(item[2][1],Text)
            digest = item[2][1].data
            assert len(digest) == 64 and all(c in b'0123456789abcdef' for c in digest)
            assert item[3][0] == 'layout' and item[3][1][0] == 'target'
            # Native header/layout binding is checked when the frontend
            # regenerates the plan. This digest is not a Lean ABI theorem.
            abi = True
        elif item[0] == "callbacks":
            assert record[1].data in (b"cesta-ownership/5", b"cesta-ownership/6", b"cesta-ownership/7") and not callbacks and len(item)>1
            callbacks = True
            groups = set()
            for group in item[1:]:
                assert len(group)>=2 and all(isinstance(x,str) for x in group)
                assert group[0] not in groups and len(set(group[1:]))==len(group)-1
                groups.add(group[0])
        elif item[0] == 'callback-interfaces':
            assert record[1].data == b'cesta-ownership/7' and not interfaces and len(item)>1
            for interface in item[1:]:
                assert len(interface)==2 and all(isinstance(x,str) for x in interface)
                name, target = map(str,interface)
                assert name not in interfaces and target == '@callback/' + name
                interfaces[name] = target
        elif item[0] == 'callback-implementations':
            assert record[1].data == b'cesta-ownership/7' and not implementations and len(item)>1
            for implementation in item[1:]:
                assert len(implementation)==2 and all(isinstance(x,str) for x in implementation)
                pair = tuple(map(str,implementation))
                assert pair not in implementations
                implementations.append(pair)
        elif item[0] == "foreign":
            assert len(item) == 4 and item[3][0] == "obligation" and len(item[3]) == 2 and isinstance(item[3][1],Text)
            foreign.append((str(item[1]),signature(item[2]),item[3][1].data.decode()))
        else:
            function(item)  # refuse unknown syntax before producing a proof file
            parameters, inputs = item[2][1][1:], item[3][1:]
            whole = {str(root) for parameter, root in zip(parameters, inputs)
                     if parameter[0] in ('viewed', 'optional-viewed')}
            if whole:
                assert record[1].data in (b'cesta-ownership/4', b'cesta-ownership/5', b'cesta-ownership/6', b'cesta-ownership/7'), 'whole-view bodies need explicit extent units'
            for event in walk(item[6][1:]):
                if event[0] in ('once', 'iteration'):
                    assert record[1].data in (b'cesta-ownership/6', b'cesta-ownership/7'), 'post-test control requires version six or seven'
                if event[0] == 'whole-root':
                    assert str(event[1]) in whole, 'whole root must name a declared viewed input'
                elif event[0] == 'root':
                    assert str(event[1]) not in whole, 'viewed input cannot acquire a concrete byte root'
                elif event[0] == 'whole-view':
                    assert str(event[2]) in whole, 'whole view must preserve its abstract input root'
            functions.append(item)
    assert functions
    assert callbacks or record[1].data != b"cesta-ownership/5"
    assert bool(interfaces) == (record[1].data == b'cesta-ownership/7')
    assert set(interfaces.values()) <= {name for name, _, _ in foreign}
    names = {name for name, _, _ in foreign} | {str(f[1]) for f in functions}
    assert all(name in interfaces and target in names for name, target in implementations)
    return foreign, functions


def admission_projection(record):
    """The exact part of an emitted plan represented by Lean admission.

    Cesta regenerates and checks the native ABI record, but that record is not
    a premise of the Lean permission model.  Layout-derived byte regions stay
    in each function plan, so a semantic layout change still changes this
    projection.  Making the boundary explicit lets proof artifacts survive a
    provenance-only ABI fingerprint change.
    """
    assert record[0] == "Ownership"
    return [record[0], record[1], *[item for item in record[2:] if item[0] != "abi"]]


def admission_projection_sha256(record):
    return hashlib.sha256(str(admission_projection(record)).encode()).hexdigest()


def walk(events):
    for event in events:
        yield event
        if event[0] == "branch":
            yield from walk(event[1][1:]); yield from walk(event[2][1:])
        elif event[0] == "loop":
            yield from walk(event[1:])
        elif event[0] in ("once", "iteration"):
            yield from walk(event[1][1:]); yield from walk(event[2][1:])
        elif event[0] in ("switch", "switch-total"):
            for arm in event[1:]:
                yield from walk(arm[1:])


def mutations(declaration):
    variants = []
    ambient=copy.deepcopy(declaration)
    assert ambient[4][0]=='ambient'
    ambient[4].append([Sym('99999999'),Sym('4')])
    variants.append(ambient)
    for target in ("access","borrow","end","release","move","invoke","result","call-argument","call-rights","call-extent","input-extent","view","view-return-extent","restrict","field-key","field-origin","field-extent","field-omitted","record-key","record-origin","record-extent","record-omitted","record-rights","owner-key","owner-holder","owner-family","owner-omitted","replace-view","replace-family",
                   "vacate-family","restore-view","null-owner-family","null-owner-released"):
        changed = copy.deepcopy(declaration)
        for event in walk(changed[6][1:]):
            event_name = "invoke" if target in ("call-argument", "call-rights", "call-extent") else "root" if target == "input-extent" else "result" if target == "view-return-extent" else "field-result" if target.startswith("field-") else "record-result" if target.startswith("record-") else "record-give" if target.startswith("owner-") else "replace" if target.startswith("replace-") else "vacate" if target.startswith("vacate-") else "restore" if target.startswith("restore-") else "null-owner" if target.startswith("null-owner-") else target
            if event[0] != event_name:
                continue
            if target == "access": event[1] = Sym("99999999")
            elif target == "borrow": event[2] = Sym("99999999")
            elif target == "end": event[1] = Sym("99999999")
            elif target == "release": event[2] = Sym(str(int(str(event[2]))+1))
            elif target == "move": event[1] = Sym("99999999")
            elif target == "replace-view": event[1] = Sym("99999999")
            elif target == "replace-family": event[3] = Sym(str(int(str(event[3]))+1))
            elif target in ("vacate-family", "null-owner-family"): event[2] = Sym(str(int(str(event[2]))+1))
            elif target == "restore-view": event[1] = Sym("99999999")
            # A null owner released as an allocation leaves a transfer no call takes.
            elif target == "null-owner-released": event[0] = Sym("release")
            elif target == "invoke": event[1] = Sym("unregistered_native_hook")
            elif target == "result": event[2] = Sym("99999999")
            elif target == "call-argument":
                if len(event[2]) <= 1:
                    continue
                event[2][1] = [Sym("loan"), Sym("99999999")]
            elif target == "call-rights":
                if event[1] != "read_view": continue
                event[1] = Sym("write_view")
            elif target == "call-extent":
                if event[1] != "read_item": continue
                event[1] = Sym("read_pair")
            elif target == "input-extent":
                if event[4] != "input" or event[1] not in changed[3][1:]: continue
                event[2] = Sym(str(int(str(event[2]))+1))
            elif target == "view": event[5] = Sym(str(int(str(event[5]))+1))
            elif target == "view-return-extent":
                if changed[5][1] != "view" or int(str(event[4])) == 0: continue
                event[4] = Sym(str(int(str(event[4]))-1))
            elif target == "restrict": event[4] = Sym("99999999")
            elif target in ("field-key", "record-key"): event[1] = Sym("99999999")
            elif target in ("field-origin", "record-origin"): event[3] = Sym("99999999")
            elif target in ("field-extent", "record-extent"): event[5] = Sym(str(int(str(event[5]))+1))
            elif target == "record-rights": event[6] = Sym("write" if event[6] == "read" else "read")
            elif target == "owner-key": event[1] = Sym("99999999")
            elif target == "owner-holder": event[2] = Sym("99999999")
            elif target == "owner-family": event[3] = Sym(str(int(str(event[3]))+1))
            elif target == "owner-omitted": event[:] = [Sym("null-record-result"), event[1]]
            elif target in ("field-omitted", "record-omitted"):
                # Replacing the postcondition with a valid ordinary read keeps
                # the loan check intact, but must fail the field-result contract.
                event[:] = [Sym("access"), *event[2:]]
            variants.append(changed)
            break
    return variants


# Admission in segments ----------------------------------------------------------------------------
#
# A plan whose Lean text is longer than SEGMENT_BOUND characters is admitted in segments. Its spine
# (the steps, branches and loops linked by `next`) is cut into segments of at most SEGMENT_STEPS
# steps; a branch or loop weighing more is a segment of its own, its arms or body cut the same way.
# A compiled evaluation of the model's own `check` gives the exits of every segment from the state
# where it starts. Each segment's exits are then a separate kernel decision from that state, both
# printed as Lean literals, and the lemmas of PermissionFlowSegments compose the decisions without
# running any segment again. The evaluation proposes the literals and the kernel decides each of
# them, so the bound chooses how admission is proved, never its answer.
SEGMENT_BOUND = 8000
SEGMENT_STEPS = int(os.environ.get("CESTA_OWNERSHIP_SEGMENT_STEPS", "16"))
if not 1 <= SEGMENT_STEPS <= 256:
    raise ValueError("CESTA_OWNERSHIP_SEGMENT_STEPS must be between 1 and 256")
SEGMENT_MODULE = "Mettapedia.Machines.CMemory.PermissionFlowSegments"
TERMINALS = {"return": ".ret", "continue": ".continueLoop", "break": ".breakLoop", "arm-exit": ".done"}


def spine(events):
    """The plan of `events` as (nodes, terminal), node for node as `body` prints it. A node is
    ("step", action), ("branch", yes, no) or ("loop", body); a mandatory iteration stays in the
    terminal, as one application."""
    nodes = []
    for position, event in enumerate(events):
        name = str(event[0])
        if name in TERMINALS:
            assert len(event) == 1 and position == len(events)-1
            return nodes, TERMINALS[name]
        if name == "branch":
            nodes.append(("branch", spine(event[1][1:]), spine(event[2][1:])))
        elif name == "loop":
            nodes.append(("loop", spine(event[1:])))
        elif name in ("once", "iteration"):
            return nodes, body(events[position:])
        elif name == "switch":
            choice = ([], ".done")
            for arm in reversed(event[1:]):
                choice = ([("branch", spine(arm[1:]), choice)], ".done")
            nodes.append(("branch", choice, ([], ".done")))
        elif name == "switch-total":
            arms = event[1:]
            choice = spine(arms[-1][1:])
            if len(arms) == 1:
                nodes.append(("branch", choice, choice))
            else:
                for arm in reversed(arms[1:-1]):
                    choice = ([("branch", spine(arm[1:]), choice)], ".done")
                nodes.append(("branch", spine(arms[0][1:]), choice))
        else:
            nodes.append(("step", action(event)))
    return nodes, ".done"


def render(plan, tail=None):
    """The Lean text of a plan; `tail` replaces its final `.done`, as `seq` does."""
    nodes, text = plan
    if tail is not None and text == ".done":
        text = tail
    for node in reversed(nodes):
        if node[0] == "step":
            text = "(.step "+node[1]+" "+text+")"
        elif node[0] == "branch":
            text = "(.branch "+render(node[1])+" "+render(node[2])+" "+text+")"
        else:
            text = "(.loop "+render(node[1])+" "+text+")"
    return text


def weight(plan):
    """The steps a check of the plan passes, counting arms and loop bodies."""
    nodes, terminal = plan
    return sum(node_weight(node) for node in nodes)+terminal.count("(.step ")


def node_weight(node):
    return 1 if node[0] == "step" else 1+sum(weight(part) for part in node[1:])


def cut_spine(plan):
    """Segments of a plan's spine: ("light", plan) for runs of at most SEGMENT_STEPS steps, ending
    in `.done` except the last, which ends in the spine's terminal; ("branch", yes, no) or ("loop",
    body) for a node weighing more, its arms or body cut in turn."""
    nodes, terminal = plan
    parts, run, load = [], [], 0
    for node in nodes:
        w = node_weight(node)
        if node[0] != "step" and w > SEGMENT_STEPS:
            if run:
                parts.append(("light", (run, ".done")))
                run, load = [], 0
            parts.append((node[0], *map(cut_spine, node[1:])))
            continue
        if run and load+w > SEGMENT_STEPS:
            parts.append(("light", (run, ".done")))
            run, load = [], 0
        run.append(node)
        load += w
    if run and terminal.count("(.step ") > SEGMENT_STEPS:
        parts.append(("light", (run, ".done")))
        run = []
    if run or not parts or terminal != ".done":
        parts.append(("light", (run, terminal)))
    return parts


def render_cut(parts, tail=None):
    """The Lean text of a cut spine with every `seq` unfolded: the plan it was cut from."""
    text = tail
    for part in reversed(parts):
        if part[0] == "light":
            text = render(part[1], text)
        else:
            arms = " ".join(render_cut(arm) for arm in part[1:])
            text = "(."+part[0]+" "+arms+" "+(text or ".done")+")"
    return text


class Segments:
    """Lean names of the segment plans of one function and its altered plans. A light segment is
    its plan text, a heavy one a branch or loop over the plans of its cut arms or body, and a cut
    spine is `seq` of its segments, one definition per distinct suffix."""

    def __init__(self):
        self.names, self.definitions, self.kinds, self.suffixes = {}, [], {}, {}

    def define(self, text, kind):
        if text not in self.names:
            self.names[text] = f"g{len(self.names)}"
            self.definitions.append((self.names[text], text))
            self.kinds[self.names[text]] = kind
        return self.names[text]

    def suffix(self, names):
        if len(names) == 1:
            return names[0]
        if names not in self.suffixes:
            tail = self.suffix(names[1:])
            self.suffixes[names] = f"q{len(self.suffixes)}"
            self.definitions.append((self.suffixes[names], f"seq {names[0]} {tail}"))
        return self.suffixes[names]

    def spine(self, parts):
        """(plan name, segment names, oracle term) of a cut spine."""
        cuts, names = [], []
        for part in parts:
            if part[0] == "light":
                name = self.define(render(part[1]), ("light",))
                cuts.append(f'(.light "{name}" {name})')
            else:
                arms = [self.spine(arm) for arm in part[1:]]
                name = self.define("(."+part[0]+" "+" ".join(arm[0] for arm in arms)+" .done)",
                                   (part[0], *(arm[1] for arm in arms)))
                cuts.append(f'(.{part[0]} "{name}" {name} '+" ".join(arm[2] for arm in arms)+")")
            names.append(name)
        plan = self.suffix(tuple(names))
        return plan, tuple(names), f'(.mk "{plan}" {plan} [{", ".join(cuts)}])'


# The compiled evaluation of `check` along cut spines. It prints the start state and the exits of
# every segment it reaches; each such line is only a proposal, which a kernel decision must confirm.
ORACLE = r'''open Mettapedia.Machines.CMemory.PermissionFlow

mutual
inductive OracleCut where
  | light (name : String) (plan : Plan Action)
  | branch (name : String) (plan : Plan Action) (yes no : OracleSpine)
  | loop (name : String) (plan : Plan Action) (body : OracleSpine)
inductive OracleSpine where
  | mk (name : String) (plan : Plan Action) (cuts : List OracleCut)
end

def oracleText {α : Type} [Repr α] (value : α) : String := Std.Format.pretty (repr value) 1000000000

def oraclePrint (kind name : String) (s : State) (result : Option (List (Exit State))) : IO Unit :=
  IO.println (kind ++ "\t" ++ name ++ "\t" ++ oracleText s ++ "\t" ++ oracleText result)

mutual
partial def oracleCut (t : State → Action → Option State) (ok : State → Bool) :
    OracleCut → State → IO (Option (List (Exit State)))
  | .light name plan, s => do
      let result := check t ok plan s
      oraclePrint "CUT" name s result
      pure result
  | .branch name plan yes no, s => do
      if (← oracleSpine t ok yes s).isSome then
        let _ ← oracleSpine t ok no s
      let result := check t ok plan s
      oraclePrint "CUT" name s result
      pure result
  | .loop name plan body, s => do
      let _ ← oracleSpine t ok body s
      let result := check t ok plan s
      oraclePrint "CUT" name s result
      pure result
partial def oracleWalk (t : State → Action → Option State) (ok : State → Bool) :
    List OracleCut → State → IO (Option (List (Exit State)))
  | [], s => pure (some [⟨.normal, s⟩])
  | [cut], s => oracleCut t ok cut s
  | cut :: rest, s => do
      match ← oracleCut t ok cut s with
      | none => pure none
      | some exits =>
          let mut out : List (Exit State) := []
          for e in exits do
            if e.kind == .normal then
              match ← oracleWalk t ok rest e.state with
              | none => return none
              | some more => out := out ++ more
            else out := out ++ [e]
          pure (some out)
partial def oracleSpine (t : State → Action → Option State) (ok : State → Bool) :
    OracleSpine → State → IO (Option (List (Exit State)))
  | .mk name plan cuts, s => do
      let walked ← oracleWalk t ok cuts s
      let result := check t ok plan s
      if walked != result then IO.println ("MISMATCH\t" ++ name)
      oraclePrint "SPINE" name s result
      pure result
end
'''

REPR_TOKEN = re.compile(r"\s*(?:(:=)|([{}\[\](),])|([A-Za-z_][A-Za-z0-9_.]*)|([0-9]+))")


def parse_repr(text):
    """A value printed by a derived `Repr`, as a tree. Structure fields come in declaration order;
    constructor names lose their namespace, which the expected type of a literal restores."""
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = REPR_TOKEN.match(text, position)
        assert match and match.end() > position, ("unexpected Repr text", text[position:position+80])
        tokens.append(next(group for group in match.groups() if group is not None))
        position = match.end()
    position = 0

    def items(close):
        nonlocal position
        out = []
        if tokens[position] == close:
            position += 1
            return out
        while True:
            out.append(value())
            position += 1
            if tokens[position-1] == close:
                return out
            assert tokens[position-1] == ",", tokens[position-1]

    def value():
        nonlocal position
        token = tokens[position]
        position += 1
        if token == "{":
            fields = []
            while True:
                assert tokens[position+1] == ":=", tokens[position:position+3]
                position += 2
                fields.append(value())
                position += 1
                if tokens[position-1] == "}":
                    return ("structure", fields)
                assert tokens[position-1] == ",", tokens[position-1]
        if token == "[":
            return ("list", items("]"))
        if token == "(":
            inside = items(")")
            return ("tuple", inside) if len(inside) > 1 else inside[0]
        if token == "some":
            return ("some", value())
        if token in ("none", "true", "false") or token.isdigit():
            return ("atom", token)
        assert "." in token, ("unexpected Repr atom", token)
        return ("atom", "."+token.rsplit(".", 1)[1])
    tree = value()
    assert position == len(tokens), tokens[position:position+5]
    return tree


def literal(tree):
    """A parsed value as a Lean literal, structures as anonymous constructors."""
    kind, data = tree
    if kind == "structure":
        return "⟨"+", ".join(map(literal, data))+"⟩"
    if kind == "list":
        return "["+", ".join(map(literal, data))+"]"
    if kind == "tuple":
        return "("+", ".join(map(literal, data))+")"
    if kind == "some":
        return "(some "+literal(data)+")"
    return data


def outcome(exits):
    return "none" if exits is None else "some ["+", ".join(f"⟨{k}, {s}⟩" for k, s in exits)+"]"


class Cuts:
    """Kernel decisions of the segments of one function, and their compositions."""

    def __init__(self, fn, segments, initial):
        self.segments = segments
        self.checked = f"check (enabled callables {fn}) (functionReturnAllowed {fn})"
        self.states = {literal(parse_repr(initial)): "initial"}
        self.definitions, self.lemmas = [], []
        self.cut, self.decided = {}, {}

    def state(self, text):
        if text not in self.states:
            self.states[text] = f"st{len(self.states)-1}"
            self.definitions.append(f"def {self.states[text]} : State := {text}")
        return self.states[text]

    def exits(self, text):
        tree = parse_repr(text)
        if tree == ("atom", "none"):
            return None
        assert tree[0] == "some" and tree[1][0] == "list", text[:80]
        out = []
        for exit in tree[1][1]:
            assert exit[0] == "structure" and len(exit[1]) == 2 and exit[1][0][0] == "atom"
            out.append((exit[1][0][1], self.state(literal(exit[1][1]))))
        return out

    def theorem(self, name, plan, s, exits, proof):
        self.lemmas.append(f"theorem {name} : {self.checked} {plan} {s} = {outcome(exits)} :=\n  {proof}")

    def read(self, lines):
        """Decide every segment of one evaluation; returns its whole plan's (plan, state, exits)."""
        whole = None
        for kind, name, s, result in lines:
            s, exits = self.state(literal(parse_repr(s))), self.exits(result)
            if kind == "SPINE":
                whole = (name, s, exits)
            elif (name, s) in self.cut:
                assert self.cut[(name, s)][1] == exits, (name, s)
            else:
                shape = self.segments.kinds[name]
                if shape[0] == "light":
                    proof = "by decide +kernel"
                else:
                    # A heavy segment from the decisions of its arms or body; a failing arm decides it.
                    used = []
                    for names in dict.fromkeys(shape[1:]):
                        lemma, arm = self.spine(names, 0, s)
                        used.append(lemma)
                        if arm is None:
                            break
                    equation = "check_branch_eq" if shape[0] == "branch" else "check_loop_eq"
                    proof = f"by\n  unfold {name}\n  rw [{', '.join([equation]+used)}]\n  decide +kernel"
                self.theorem(f"{name}_{s}", name, s, exits, proof)
                self.cut[(name, s)] = (f"{name}_{s}", exits)
        return whole

    def spine(self, names, i, s):
        """(lemma, exits) deciding the suffix `names[i:]` of a cut spine from `s`."""
        if i == len(names)-1:
            return self.cut[(names[i], s)]
        plan = self.segments.suffix(names[i:])
        if (plan, s) not in self.decided:
            cut, exits = self.cut[(names[i], s)]
            if exits is None:
                proof, result = f"check_seq_none {cut}", None
            else:
                continued, result = self.continued(names, i+1, exits)
                proof = f"(check_seq_of {cut}).trans\n    {continued}"
            self.theorem(f"{plan}_{s}", plan, s, result, proof)
            self.decided[(plan, s)] = (f"{plan}_{s}", result)
        return self.decided[(plan, s)]

    def continued(self, names, i, exits):
        """`continueExits` of the suffix `names[i:]` over `exits`: (proof, exits)."""
        if not exits:
            return "(continue_nil _)", []
        (kind, s), rest = exits[0], exits[1:]
        if kind == ".normal":
            lemma, result = self.spine(names, i, s)
            if result is None:
                return f"(continue_normal_none {lemma})", None
            later, more = self.continued(names, i, rest)
            return f"(continue_normal_eq {lemma} {later})", None if more is None else result+more
        later, more = self.continued(names, i, rest)
        return f"(continue_abrupt_eq (by decide) {later})", None if more is None else [(kind, s)]+more


def segmented_admission(index, item, altered, evaluate):
    """Declarations admitting `fn{index}` in segments, and refusals of its altered bodies."""
    fn = f"fn{index}"
    segments = Segments()
    variants, own = [], 0
    for variant in [item]+altered:
        events = variant[6][1:]
        parts = cut_spine(spine(events))
        assert render_cut(parts) == body(events), "the segments must compose to the plan"
        variants.append((variant, *segments.spine(parts)))
        if len(variants) == 1:
            own = len(segments.kinds)
    definitions = [f"def {name} : Plan Action := {text}" for name, text in segments.definitions]
    run = ['#eval show IO Unit from do', '  IO.println ("INITIAL\\t" ++ oracleText initial)']
    for k, (_, plan, _, tree) in enumerate(variants):
        run += [f'  IO.println "BEGIN\\t{k}"',
                f'  let _ ← oracleSpine (enabled callables {fn}) (functionReturnAllowed {fn}) {tree} initial']
    output = evaluate(f"Cuts{index}", [f"def {fn} : Function := "+function(item), ORACLE]+definitions+["\n".join(run)])
    lines = [line.split("\t") for line in output.splitlines()
             if line.split("\t", 1)[0] in ("INITIAL", "BEGIN", "CUT", "SPINE", "MISMATCH")]
    assert lines and lines[0][0] == "INITIAL" and not any(line[0] == "MISMATCH" for line in lines), output[-4000:]
    cuts = Cuts(fn, segments, lines[0][1])
    runs = []
    for line in lines[1:]:
        if line[0] == "BEGIN":
            assert int(line[1]) == len(runs)
            runs.append([])
        else:
            assert len(line) == 4
            runs[-1].append(tuple(line))
    assert len(runs) == len(variants)
    decided = []
    for (variant, plan, names, _), lines in zip(variants, runs):
        whole = cuts.read(lines)
        decided.append(cuts.spine(names, 0, "initial"))
        assert whole == (plan, "initial", decided[-1][1]), (whole, decided[-1])
    out = [f"namespace Segments{index}", "open Mettapedia.Machines.CMemory.PermissionFlow"]
    out += definitions+cuts.definitions+cuts.lemmas
    out += [f"theorem body : {fn}.body = {variants[0][1]} := rfl",
            f"theorem checked : {cuts.checked} {fn}.body initial = {outcome(decided[0][1])} := by\n"
            f"  rw [body]\n  exact {decided[0][0]}"]
    refusals = []
    for k, ((variant, plan, _, _), (lemma, exits)) in enumerate(zip(variants[1:], decided[1:])):
        out += [f"def altered{k} : Function := {{ {fn} with body := {body(variant[6][1:])} }}",
                f"theorem altered{k}_body : altered{k}.body = {plan} := rfl",
                f"theorem altered{k}_checked : {cuts.checked} altered{k}.body initial = {outcome(exits)} := by\n"
                f"  rw [altered{k}_body]\n  exact {lemma}"]
        name = f"Segments{index}.altered{k}"
        refusals.append(f"example : admitFunction callables {name} = false := by\n  unfold admitFunction\n"
            f"  rw [show enabled callables {name} = enabled callables {fn} from rfl,\n"
            f"    show functionReturnAllowed {name} = functionReturnAllowed {fn} from rfl, {name}_checked]\n"
            f"  decide +kernel")
    out.append(f"end Segments{index}")
    out.append(f"theorem accepts{index} : admitFunction callables {fn} = true := by\n  unfold admitFunction\n"
               f"  rw [Segments{index}.checked]\n  decide +kernel")
    return out, refusals, own


def admit_record(record, path, lean_root, controls=True, cache=True, assemble=True, jobs=1):
    path = Path(path).resolve()
    foreign, functions = module_parts(record)
    names = [name for name,_,_ in foreign]+[str(f[1]) for f in functions]
    assert len(names) == len(set(names))
    # Artifact basenames are file names, not Lean identifiers. Keep the module
    # name valid for spaces, hyphens, Unicode and leading digits alike.
    prefix = "Ownership"+hashlib.sha256(path.name.encode()).hexdigest()[:20]+"Parts"
    directory = path.parent/prefix
    directory.mkdir(parents=True,exist_ok=True)
    environment = dict(os.environ)
    environment["LEAN_PATH"] = str(path.parent.resolve())+os.pathsep+environment.get("LEAN_PATH","")
    from lean_contracts import OWNERSHIP_MODULES
    # Function bodies belong to their own proof artifacts, not this shared
    # context.  Keeping them here made one changed body invalidate every part.
    # Names.lean below supplies the exact callable-signature dependency; the
    # aggregate theorem is separately keyed by all compiled part artifacts.
    context=hashlib.sha256(b"ownership-admission-cache/2")
    context.update(Path(__file__).read_bytes())
    context.update(b'controls-on' if controls else b'controls-off')
    context.update(str(lean_root.resolve()).encode())
    context.update(environment["LEAN_PATH"].encode())
    context.update((lean_root/'lean-toolchain').read_bytes())
    for name in OWNERSHIP_MODULES:
        artifact=lean_root/'.lake/build/lib/lean'/(name.replace('.','/')+'.olean')
        context.update(name.encode());context.update(artifact.read_bytes())
    context_key=context.hexdigest()
    cached=0
    cache_lock=threading.Lock()
    imports = ["import Mettapedia.Ownership.ViewExtents",
               "import Mettapedia.Ownership.Callbacks"]
    setup = ["open Mettapedia.Ownership", "set_option maxRecDepth 200000",
        "set_option maxHeartbeats 0", "namespace "+prefix]
    def compile_file(source, text, export=True):
        nonlocal cached
        data=text.encode()
        artifact=source.with_suffix('.olean')
        checkpoint=source.with_suffix('.checked.json')
        source_hash=hashlib.sha256(data).hexdigest()
        if cache and export and source.is_file() and artifact.is_file() and checkpoint.is_file():
            try: previous=json.loads(checkpoint.read_text())
            except (ValueError,OSError):previous={}
            if previous.get('context')==context_key and previous.get('source')==source_hash and \
                    source.read_bytes()==data and previous.get('artifact')==hashlib.sha256(artifact.read_bytes()).hexdigest():
                with cache_lock:
                    cached+=1
                return 0.0, None
        source.write_bytes(data)
        command = ["lake","env","lean","-M","16384","--root="+str(path.parent.resolve())]
        if export:
            command += ["-o",source.with_suffix(".olean")]
        command.append(source)
        started = time.monotonic()
        with source.with_suffix(".log").open("w") as log:
            process = subprocess.Popen(list(map(str,command)),cwd=lean_root,stdout=log,stderr=subprocess.STDOUT,env=environment)
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
        peak = usage.ru_maxrss//1024   # MiB, resident, the whole Lean process tree
        if process.returncode:
            raise AssertionError((command,process.returncode,source.with_suffix(".log").read_text()[-16000:]))
        if export:
            checkpoint.write_text(json.dumps(dict(context=context_key,source=source_hash,
                artifact=hashlib.sha256(artifact.read_bytes()).hexdigest()),indent=2)+'\n')
        return round(time.monotonic()-started,3), peak
    def evaluate(name, lines):
        """The output of an unexported file of this module, cached like its parts. It proposes
        literals only: every one of them is decided again by the kernel."""
        source = directory/(name+".lean")
        data = ("\n".join(["import "+prefix+".Names", "import "+SEGMENT_MODULE]+setup+lines+["end "+prefix])+"\n").encode()
        record = source.with_suffix(".output.json")
        key = hashlib.sha256(context_key.encode()+data).hexdigest()
        if cache and record.is_file():
            try: previous = json.loads(record.read_text())
            except (ValueError,OSError): previous = {}
            if previous.get("key") == key:
                return previous["output"]
        source.write_bytes(data)
        command = ["lake","env","lean","-M","16384","--root="+str(path.parent.resolve()),source]
        result = subprocess.run(list(map(str,command)),cwd=lean_root,capture_output=True,text=True,env=environment)
        if result.returncode:
            raise AssertionError((command,result.returncode,(result.stdout+result.stderr)[-16000:]))
        record.write_text(json.dumps(dict(key=key,output=result.stdout))+"\n")
        return result.stdout
    signatures = [(n,s) for n,s,_ in foreign]+[(str(f[1]),signature(f[2])) for f in functions]
    callback_checks = []
    for item in record[2:]:
        if item[0] != "callbacks": continue
        for index, group in enumerate(item[1:]):
            targets = [str(n) for n in group[1:]]
            assert all(n in names for n in targets), "callback target is absent from the module"
            contract = dict(signatures)[targets[0]]
            callback_checks.append("theorem callback"+str(index)+" : Callbacks.agrees callables ("+
                contract+") ["+", ".join(json.dumps(n) for n in targets)+"] = true := by decide")
    interface_targets = {str(group[0]):str(group[1]) for item in record[2:]
                         if item[0] == 'callback-interfaces' for group in item[1:]}
    for index, (name, target) in enumerate(interface_targets.items()):
        contract = dict(signatures)[target]
        callback_checks.append(f'theorem interface{index} : signatureValid ({contract}) = true := by decide')
    implementation_index = 0
    for item in record[2:]:
        if item[0] != 'callback-implementations':
            continue
        for name, target in item[1:]:
            contract = dict(signatures)[interface_targets[str(name)]]
            callback_checks.append(f'theorem implementation{implementation_index} : '
                f'Callbacks.acceptsSignature callables ({contract}) {json.dumps(str(target))} = true := by decide')
            implementation_index += 1
    names_text = "\n".join(imports+setup+[
        "def callables : List Callable := ["+", ".join("⟨"+json.dumps(n)+", "+s+"⟩" for n,s in
            signatures)+ "]"]+callback_checks+["end "+prefix])+"\n"
    compile_file(directory/"Names.lean", names_text)
    names_context = hashlib.sha256()
    names_context.update(context_key.encode())
    names_context.update(hashlib.sha256(names_text.encode()).digest())
    context_key = names_context.hexdigest()
    checks = rejected = 0
    timings = []
    part_artifacts = []
    part_jobs = []
    only = set(n for n in os.environ.get("ADMIT_ONLY", "").split(",") if n)   # scratch only
    for index, item in enumerate(functions):
        if only and str(item[1]) not in only:
            continue
        segmented = len(function(item)) > SEGMENT_BOUND
        cut = evaluated = None
        if segmented:
            # Altered bodies are refused segment by segment too; other alterations as before.
            started = time.monotonic()
            altered = [m for m in mutations(item) if m[:6] == item[:6]] if controls else []
            # Segments are decided one after another, so one segment's kernel memory is held at a time.
            declarations = ["import "+prefix+".Names", "import "+SEGMENT_MODULE]+setup+["set_option Elab.async false"]
            declarations.append(f"def fn{index} : Function := "+function(item))
            proofs, refusals, cut = segmented_admission(index, item, altered, evaluate)
            declarations += proofs
            evaluated = round(time.monotonic()-started,3)
        else:
            declarations = ["import "+prefix+".Names"]+setup
            declarations.append(f"def fn{index} : Function := "+function(item))
            declarations.append(f"theorem accepts{index} : admitFunction callables fn{index} = true := by decide +kernel")
        if record[1].data in (b"cesta-ownership/4", b"cesta-ownership/5", b"cesta-ownership/6", b"cesta-ownership/7"):
            declarations.append(f"theorem extent_prefix{index} (bytes : Nat → Nat) (reached : State) "
                f"(path : Mettapedia.Machines.CMemory.PermissionFlow.Prefix "
                f"(enabled callables fn{index}) fn{index}.body initial reached) : "
                f"Valid (scaleState (viewWeight fn{index} bytes) reached) := "
                f"prefix_valid_scale (viewWeight fn{index} bytes) path")
        checks += 1
        if controls:
            for mutant in mutations(item):
                if segmented and mutant[:6] == item[:6]:
                    declarations.append(refusals.pop(0))
                else:
                    declarations.append(f"example : admitFunction callables ({function(mutant)} : Function) = false := by decide +kernel")
                rejected += 1
            assert not segmented or not refusals
        declarations.append("end "+prefix)
        part_jobs.append((index, str(item[1]), segmented, cut, evaluated,
                          "\n".join(declarations)+"\n"))

    def compile_part(job):
        index, name, segmented, cut, evaluated, text = job
        source = directory/f"Part{index}.lean"
        seconds, peak = compile_file(source, text)
        artifact = hashlib.sha256(source.with_suffix(".olean").read_bytes()).digest()
        timing = {"name":name,"seconds":seconds,"peak_mib":peak}
        if segmented:
            timing.update(segments=cut,evaluation_seconds=evaluated)
        return index, name, segmented, cut, timing, artifact

    if jobs == 1:
        compiled = map(compile_part, part_jobs)
        executor = None
    else:
        executor = ThreadPoolExecutor(max_workers=jobs)
        compiled = executor.map(compile_part, part_jobs)
    try:
        for index, name, segmented, cut, timing, artifact in compiled:
            timings.append(timing)
            part_artifacts.append(artifact)
            detail = f"; {cut} segments" if segmented else ""
            print(f"ownership admission: {index+1}/{len(functions)} {name} ({timing['seconds']}s{detail})",flush=True)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    if not assemble:
        return {"functions":checks,"mutants":rejected,"foreign_obligations":len(foreign),
            "plan_sha256":hashlib.sha256(str(record).encode()).hexdigest(),
            "admission_sha256":admission_projection_sha256(record),"timings":timings,
            "cached_parts":cached,"module_admitted":False}
    declarations = ["import "+prefix+f".Part{i}" for i in range(len(functions))]+setup
    declarations.append("def wholeModule : Module := ⟨["+", ".join("⟨"+json.dumps(n)+", "+s+", "+json.dumps(o)+"⟩" for n,s,o in foreign)+"], ["+", ".join(f"fn{i}" for i in range(len(functions)))+"]⟩")
    declarations.append("theorem wholeModule_admitted : admit wholeModule = true := by\n"
        "  apply admit_of_checks\n  · decide\n  · decide\n  · decide\n"
        "  · change ["+", ".join(f"fn{i}" for i in range(len(functions)))+"].all (admitFunction callables) = true\n"
        "    simp only [List.all_cons, List.all_nil, "+", ".join(f"accepts{i}" for i in range(len(functions)))+", Bool.and_self]")
    declarations.append("end "+prefix)
    assembly_context = hashlib.sha256(context_key.encode())
    for artifact in part_artifacts:
        assembly_context.update(artifact)
    context_key = assembly_context.hexdigest()
    compile_file(path,"\n".join(declarations)+"\n")
    return {"functions":checks,"mutants":rejected,"foreign_obligations":len(foreign),
        "plan_sha256":hashlib.sha256(str(record).encode()).hexdigest(),
        "admission_sha256":admission_projection_sha256(record),"timings":timings,
        "cached_parts":cached,"module_admitted":True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta",type=Path,default=ROOT/"build/cesta")
    parser.add_argument("--lean-root",type=Path,default=ROOT.parent/"Mettapedia/lean/mettapedia")
    parser.add_argument("--source",type=Path,action="append",default=[])
    parser.add_argument("--include",type=Path,action="append",default=[])
    parser.add_argument("--define",action="append",default=[])
    parser.add_argument("--output",type=Path,default=ROOT/"build/ownership-admission")
    parser.add_argument("--admission-stem",default="Admission")
    parser.add_argument("--no-cache",action="store_true")
    parser.add_argument("--jobs",type=int,default=int(os.environ.get("CESTA_OWNERSHIP_ADMISSION_JOBS", "1")))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least one")
    args.output.mkdir(parents=True,exist_ok=True)
    sources = list(args.source)
    if not sources:
        for name,(source,contract) in ownership_core.POSITIVE.items():
            path = args.output/(name+".cesta"); path.write_text(ownership_core.document(source,contract)); sources.append(path)
        sources += sorted((ROOT/"ownership/examples").glob("*.cesta"))
    results = []
    for index, source in enumerate(sources):
        record_path = args.output/f"{index}.plan"
        command = [args.cesta,"export-c",source.resolve(),"--std=c11","-o",args.output/f"{index}.c","--plan",record_path,"--"]
        command += ["-I"+str(p.resolve()) for p in args.include]+["-D"+name for name in args.define]
        run(command)
        records = [r for r in parse(record_path.read_bytes()) if r and r[0] == "Ownership"]
        assert len(records) == 1
        record = records[0]
        results.append(admit_record(record,args.output/f"{args.admission_stem}{index}.lean",args.lean_root,cache=not args.no_cache,
                                    assemble=not os.environ.get("ADMIT_ONLY"),jobs=args.jobs))
    (args.output/"result.json").write_text(json.dumps(results,indent=2)+"\n")
    print(f"ownership admission: {sum(x['functions'] for x in results)} actual function plans admitted; {sum(x['mutants'] for x in results)} altered plans refused")


if __name__ == "__main__":
    main()
