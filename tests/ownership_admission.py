"""Kernel-admit the ownership plans emitted from the actual typed C bodies.

The permission/control model is proved. C parsing, projection, native contracts
and compiler fidelity remain distinct tested or conditional boundaries.
"""

import argparse
import copy
import hashlib
import json
import os
import time
from pathlib import Path
import subprocess

import ownership_core
from oracles.pro_compact_cesta import Sym, Text, parse

ROOT = Path(__file__).resolve().parent.parent
MODEL = "Mettapedia.GSLT.LanguageDef.Ownership"


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
    if name == "root":
        assert len(event) == 6 and event[4] in ("local","input","heap","static")
        return f"(.root {natural(event[1])} {natural(event[2])} {mode(event[3])} .{event[4]} {natural(event[5])})"
    if name == "borrow":
        assert len(event) == 7
        return f"(.borrow {natural(event[1])} {natural(event[2])} {region(event[3:6])} {mode(event[6])})"
    if name in ("access","result"):
        assert len(event) == 6
        return f"(.{name} {natural(event[1])} {region(event[2:5])} {boolean(event[5])})"
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
        assert len(event) == 2
        return "(.invoke "+json.dumps(str(event[1]))+")"
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
    if name == "switch":
        # Conditions are erased to nondeterminism, including no matching arm.
        # Each arm owns its break; outer loop transfers remain distinct.
        choice = ".done"
        for arm in reversed(event[1:]):
            assert arm[0] == "arm"
            choice = "(.branch "+body(arm[1:])+" "+choice+" .done)"
        return "(.branch "+choice+" .done "+body(rest)+")"
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
    assert kind == "borrow" and len(declaration) == 7
    return "(.borrowed "+region(declaration[2:5])+" "+boolean(declaration[5])+" "+boolean(declaration[6])+")"


def function(declaration):
    assert declaration[0] == "function" and len(declaration) == 4 and declaration[3][0] == "body"
    return "⟨"+json.dumps(str(declaration[1]))+", "+result_spec(declaration[2])+", "+body(declaration[3][1:])+"⟩"


def module_parts(record):
    assert record[0] == "Ownership" and isinstance(record[1],Text) and record[1].data == b"cesta-ownership/1"
    foreign, functions = [], []
    for item in record[2:]:
        if item[0] == "foreign":
            assert len(item) == 3 and item[2][0] == "obligation" and len(item[2]) == 2 and isinstance(item[2][1],Text)
            foreign.append((str(item[1]),item[2][1].data.decode()))
        else:
            function(item)  # refuse unknown syntax before producing a proof file
            functions.append(item)
    assert functions
    return foreign, functions


def walk(events):
    for event in events:
        yield event
        if event[0] == "branch":
            yield from walk(event[1][1:]); yield from walk(event[2][1:])
        elif event[0] == "loop":
            yield from walk(event[1:])
        elif event[0] == "switch":
            for arm in event[1:]:
                yield from walk(arm[1:])


def mutations(declaration):
    variants = []
    for target in ("access","borrow","end","release","move","invoke","result"):
        changed = copy.deepcopy(declaration)
        for event in walk(changed[3][1:]):
            if event[0] != target:
                continue
            if target == "access": event[1] = Sym("99999999")
            elif target == "borrow": event[2] = Sym("99999999")
            elif target == "end": event[1] = Sym("99999999")
            elif target == "release": event[2] = Sym(str(int(str(event[2]))+1))
            elif target == "move": event[1] = Sym("99999999")
            elif target == "invoke": event[1] = Sym("unregistered_native_hook")
            elif target == "result": event[2] = Sym("99999999")
            variants.append(changed)
            break
    return variants


def admit_record(record, path, lean_root, controls=True):
    foreign, functions = module_parts(record)
    names = [name for name,_ in foreign]+[str(f[1]) for f in functions]
    assert len(names) == len(set(names))
    prefix = path.stem+"Parts"
    directory = path.parent/prefix
    directory.mkdir(parents=True,exist_ok=True)
    environment = dict(os.environ)
    environment["LEAN_PATH"] = str(path.parent.resolve())+os.pathsep+environment.get("LEAN_PATH","")
    imports = ["import Mettapedia.GSLT.LanguageDef.OwnershipAdmission"]
    setup = ["open Mettapedia.GSLT.LanguageDef.Ownership", "set_option maxRecDepth 200000",
        "set_option maxHeartbeats 0", "namespace "+prefix]
    def compile_file(source, text, export=True):
        source.write_text(text)
        command = ["lake","env","lean","--root="+str(path.parent.resolve())]
        if export:
            command += ["-o",source.with_suffix(".olean")]
        command.append(source)
        started = time.monotonic()
        with source.with_suffix(".log").open("w") as log:
            result = subprocess.run(list(map(str,command)),cwd=lean_root,stdout=log,stderr=subprocess.STDOUT,env=environment)
        if result.returncode:
            raise AssertionError((command,result.returncode,source.with_suffix(".log").read_text()[-16000:]))
        return round(time.monotonic()-started,3)
    compile_file(directory/"Names.lean","\n".join(imports+setup+[
        "def names : List String := "+json.dumps(names), "end "+prefix])+"\n")
    checks = rejected = 0
    timings = []
    for index, item in enumerate(functions):
        declarations = ["import "+prefix+".Names"]+setup
        declarations.append(f"def fn{index} : Function := "+function(item))
        declarations.append(f"theorem accepts{index} : admitFunction names fn{index} = true := by decide +kernel")
        checks += 1
        if controls:
            for mutant in mutations(item):
                declarations.append(f"example : admitFunction names ({function(mutant)} : Function) = false := by decide +kernel")
                rejected += 1
        declarations.append("end "+prefix)
        seconds = compile_file(directory/f"Part{index}.lean","\n".join(declarations)+"\n")
        timings.append({"name":str(item[1]),"seconds":seconds})
        print(f"ownership admission: {index+1}/{len(functions)} {item[1]} ({seconds}s)",flush=True)
    declarations = ["import "+prefix+f".Part{i}" for i in range(len(functions))]+setup
    declarations.append("def wholeModule : Module := ⟨["+", ".join("⟨"+json.dumps(n)+", "+json.dumps(o)+"⟩" for n,o in foreign)+"], ["+", ".join(f"fn{i}" for i in range(len(functions)))+"]⟩")
    declarations.append("theorem wholeModule_admitted : admit wholeModule = true := by\n"
        "  apply admit_of_checks\n  · decide\n  · decide\n  · decide\n"
        "  · change ["+", ".join(f"fn{i}" for i in range(len(functions)))+"].all (admitFunction names) = true\n"
        "    simp only [List.all_cons, List.all_nil, "+", ".join(f"accepts{i}" for i in range(len(functions)))+", Bool.and_self]")
    declarations.append("end "+prefix)
    compile_file(path,"\n".join(declarations)+"\n")
    return {"functions":checks,"mutants":rejected,"foreign_obligations":len(foreign),
        "plan_sha256":hashlib.sha256(str(record).encode()).hexdigest(),"timings":timings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta",type=Path,default=ROOT/"build/cesta")
    parser.add_argument("--lean-root",type=Path,default=ROOT.parent/"Mettapedia/lean/mettapedia")
    parser.add_argument("--source",type=Path,action="append",default=[])
    parser.add_argument("--include",type=Path,action="append",default=[])
    parser.add_argument("--define",action="append",default=[])
    parser.add_argument("--output",type=Path,default=ROOT/"build/ownership-admission")
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    sources = list(args.source)
    if not sources:
        for name,(source,contract) in ownership_core.POSITIVE.items():
            path = args.output/(name+".cesta"); path.write_text(ownership_core.document(source,contract)); sources.append(path)
    results = []
    for index, source in enumerate(sources):
        record_path = args.output/f"{index}.plan"
        command = [args.cesta,"export-c",source.resolve(),"--std=c11","-o",args.output/f"{index}.c","--plan",record_path,"--"]
        command += ["-I"+str(p.resolve()) for p in args.include]+["-D"+name for name in args.define]
        run(command)
        records = [r for r in parse(record_path.read_bytes()) if r and r[0] == "Ownership"]
        assert len(records) == 1
        record = records[0]
        results.append(admit_record(record,args.output/f"Admission{index}.lean",args.lean_root))
    (args.output/"result.json").write_text(json.dumps(results,indent=2)+"\n")
    print(f"ownership admission: {sum(x['functions'] for x in results)} actual function plans admitted; {sum(x['mutants'] for x in results)} altered plans refused")


if __name__ == "__main__":
    main()
