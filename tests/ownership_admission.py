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
    if name == "field-result":
        assert len(event) == 7
        return f"(.fieldResult {natural(event[1])} {natural(event[2])} {region(event[3:6])} {boolean(event[6])})"
    if name == "null-field-result":
        assert len(event) == 2
        return f"(.nullFieldResult {natural(event[1])})"
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
    if kind == "view":
        assert len(declaration) == 6
        return "(.viewed "+region(declaration[2:5])+" "+boolean(declaration[5])+")"
    assert kind == "borrow" and len(declaration) == 7
    return "(.borrowed "+region(declaration[2:5])+" "+boolean(declaration[5])+" "+boolean(declaration[6])+")"


def signature(declaration):
    assert declaration[0] == "signature" and len(declaration) in (4, 5)
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
        else:
            assert p[0] == "released" and len(p) == 2
            ps.append("(.released "+natural(p[1])+")")
    assert len(variadic) == 2 and variadic[1] in ("read", "none")
    assert result[0] == "result"
    if result[1] == "viewed":
        assert len(result) == 6
        returns = "(.viewed "+" ".join(natural(n) for n in result[2:5])+" "+boolean(result[5])+")"
    elif result[1] == "borrowed":
        assert len(result) == 7
        returns = "(.borrowed "+" ".join(natural(n) for n in result[2:5])+" "+boolean(result[5])+" "+boolean(result[6])+")"
    else:
        returns = result_spec([Sym("returns"), Sym("own") if result[1] == "owned" else result[1], *result[2:]])
    fields = []
    if len(declaration) == 5:
        assert declaration[4][0] == "fields"
        for field in declaration[4][1:]:
            assert len(field) == 3 and field[0] == "field"
            shape = field[2]
            if shape[0] == "static":
                assert len(shape) == 2
                code = "(.static "+boolean(shape[1])+")"
            else:
                assert shape[0] == "borrowed" and len(shape) == 6
                code = "(.borrowed "+" ".join(natural(n) for n in shape[1:4])+" "+boolean(shape[4])+" "+boolean(shape[5])+")"
            fields.append("("+natural(field[1])+", "+code+")")
    return "⟨["+", ".join(ps)+"], "+("true" if variadic[1] == "read" else "false")+", "+returns+", ["+", ".join(fields)+"]⟩"


def function(declaration):
    assert declaration[0] == "function" and len(declaration) == 7 and declaration[6][0] == "body"
    assert declaration[3][0] == "inputs" and declaration[4][0] == "ambient"
    inputs = "["+", ".join(natural(n) for n in declaration[3][1:])+"]"
    ambient = "["+", ".join("("+natural(p[0])+", "+natural(p[1])+")" for p in declaration[4][1:])+"]"
    return "⟨"+json.dumps(str(declaration[1]))+", "+signature(declaration[2])+", "+inputs+", "+ambient+", "+result_spec(declaration[5])+", "+body(declaration[6][1:])+"⟩"


def module_parts(record):
    assert record[0] == "Ownership" and isinstance(record[1],Text) and record[1].data == b"cesta-ownership/2"
    foreign, functions = [], []
    for item in record[2:]:
        if item[0] == "foreign":
            assert len(item) == 4 and item[3][0] == "obligation" and len(item[3]) == 2 and isinstance(item[3][1],Text)
            foreign.append((str(item[1]),signature(item[2]),item[3][1].data.decode()))
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
    ambient=copy.deepcopy(declaration)
    assert ambient[4][0]=='ambient'
    ambient[4].append([Sym('99999999'),Sym('4')])
    variants.append(ambient)
    for target in ("access","borrow","end","release","move","invoke","result","call-argument","call-rights","call-extent","input-extent","view","view-return-extent","restrict","field-key","field-origin","field-extent","field-omitted"):
        changed = copy.deepcopy(declaration)
        for event in walk(changed[6][1:]):
            event_name = "invoke" if target in ("call-argument", "call-rights", "call-extent") else "root" if target == "input-extent" else "result" if target == "view-return-extent" else "field-result" if target.startswith("field-") else target
            if event[0] != event_name:
                continue
            if target == "access": event[1] = Sym("99999999")
            elif target == "borrow": event[2] = Sym("99999999")
            elif target == "end": event[1] = Sym("99999999")
            elif target == "release": event[2] = Sym(str(int(str(event[2]))+1))
            elif target == "move": event[1] = Sym("99999999")
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
            elif target == "field-key": event[1] = Sym("99999999")
            elif target == "field-origin": event[3] = Sym("99999999")
            elif target == "field-extent": event[5] = Sym(str(int(str(event[5]))+1))
            elif target == "field-omitted":
                # Replacing the postcondition with a valid ordinary read keeps
                # the loan check intact, but must fail the field-result contract.
                event[:] = [Sym("access"), *event[2:]]
            variants.append(changed)
            break
    return variants


def admit_record(record, path, lean_root, controls=True, cache=True):
    foreign, functions = module_parts(record)
    names = [name for name,_,_ in foreign]+[str(f[1]) for f in functions]
    assert len(names) == len(set(names))
    prefix = path.stem+"Parts"
    directory = path.parent/prefix
    directory.mkdir(parents=True,exist_ok=True)
    environment = dict(os.environ)
    environment["LEAN_PATH"] = str(path.parent.resolve())+os.pathsep+environment.get("LEAN_PATH","")
    from lean_contracts import OWNERSHIP_MODULES
    context=hashlib.sha256(str(record).encode())
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
    imports = ["import Mettapedia.GSLT.LanguageDef.OwnershipAdmission"]
    setup = ["open Mettapedia.GSLT.LanguageDef.Ownership", "set_option maxRecDepth 200000",
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
                cached+=1
                return 0.0
        source.write_bytes(data)
        command = ["lake","env","lean","--root="+str(path.parent.resolve())]
        if export:
            command += ["-o",source.with_suffix(".olean")]
        command.append(source)
        started = time.monotonic()
        with source.with_suffix(".log").open("w") as log:
            result = subprocess.run(list(map(str,command)),cwd=lean_root,stdout=log,stderr=subprocess.STDOUT,env=environment)
        if result.returncode:
            raise AssertionError((command,result.returncode,source.with_suffix(".log").read_text()[-16000:]))
        if export:
            checkpoint.write_text(json.dumps(dict(context=context_key,source=source_hash,
                artifact=hashlib.sha256(artifact.read_bytes()).hexdigest()),indent=2)+'\n')
        return round(time.monotonic()-started,3)
    compile_file(directory/"Names.lean","\n".join(imports+setup+[
        "def callables : List Callable := ["+", ".join("⟨"+json.dumps(n)+", "+s+"⟩" for n,s in
            [(n,s) for n,s,_ in foreign]+[(str(f[1]),signature(f[2])) for f in functions])+ "]", "end "+prefix])+"\n")
    checks = rejected = 0
    timings = []
    for index, item in enumerate(functions):
        declarations = ["import "+prefix+".Names"]+setup
        declarations.append(f"def fn{index} : Function := "+function(item))
        declarations.append(f"theorem accepts{index} : admitFunction callables fn{index} = true := by decide +kernel")
        checks += 1
        if controls:
            for mutant in mutations(item):
                declarations.append(f"example : admitFunction callables ({function(mutant)} : Function) = false := by decide +kernel")
                rejected += 1
        declarations.append("end "+prefix)
        seconds = compile_file(directory/f"Part{index}.lean","\n".join(declarations)+"\n")
        timings.append({"name":str(item[1]),"seconds":seconds})
        print(f"ownership admission: {index+1}/{len(functions)} {item[1]} ({seconds}s)",flush=True)
    declarations = ["import "+prefix+f".Part{i}" for i in range(len(functions))]+setup
    declarations.append("def wholeModule : Module := ⟨["+", ".join("⟨"+json.dumps(n)+", "+s+", "+json.dumps(o)+"⟩" for n,s,o in foreign)+"], ["+", ".join(f"fn{i}" for i in range(len(functions)))+"]⟩")
    declarations.append("theorem wholeModule_admitted : admit wholeModule = true := by\n"
        "  apply admit_of_checks\n  · decide\n  · decide\n  · decide\n"
        "  · change ["+", ".join(f"fn{i}" for i in range(len(functions)))+"].all (admitFunction callables) = true\n"
        "    simp only [List.all_cons, List.all_nil, "+", ".join(f"accepts{i}" for i in range(len(functions)))+", Bool.and_self]")
    declarations.append("end "+prefix)
    compile_file(path,"\n".join(declarations)+"\n")
    return {"functions":checks,"mutants":rejected,"foreign_obligations":len(foreign),
        "plan_sha256":hashlib.sha256(str(record).encode()).hexdigest(),"timings":timings,"cached_parts":cached}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta",type=Path,default=ROOT/"build/cesta")
    parser.add_argument("--lean-root",type=Path,default=ROOT.parent/"Mettapedia/lean/mettapedia")
    parser.add_argument("--source",type=Path,action="append",default=[])
    parser.add_argument("--include",type=Path,action="append",default=[])
    parser.add_argument("--define",action="append",default=[])
    parser.add_argument("--output",type=Path,default=ROOT/"build/ownership-admission")
    parser.add_argument("--no-cache",action="store_true")
    args = parser.parse_args()
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
        results.append(admit_record(record,args.output/f"Admission{index}.lean",args.lean_root,cache=not args.no_cache))
    (args.output/"result.json").write_text(json.dumps(results,indent=2)+"\n")
    print(f"ownership admission: {sum(x['functions'] for x in results)} actual function plans admitted; {sum(x['mutants'] for x in results)} altered plans refused")


if __name__ == "__main__":
    main()
