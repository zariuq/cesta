"""Filewise native compiler and codec qualification of external C corpora."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import traceback


ROOT = Path(__file__).resolve().parent.parent
BUILD_EPOCH = "1791633600"


def run(arguments, cwd=ROOT, data=None):
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = BUILD_EPOCH
    result = subprocess.run(arguments, cwd=cwd, input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240,
                            env=environment)
    lines = result.stderr.decode(errors="replace").splitlines()
    messages = [line[:4096] for line in lines if not re.match(r"^\s*(?:\d+\s*\||[|^~])", line)]
    return {"accepted": result.returncode == 0, "status": result.returncode,
            "diagnostic": "\n".join(messages)}, result.stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def profile_sources(path):
    base = path.resolve().parent
    files = {}
    pending = [path.resolve()]
    while pending:
        current = pending.pop()
        if not current.is_relative_to(base):
            raise RuntimeError("qualification profile includes must remain in the selected profile directory")
        name = str(current.relative_to(base))
        if name in files:
            continue
        data = current.read_bytes()
        files[name] = data
        pending.extend((current.parent / item).resolve() for item in
                       re.findall(r'^\s*include\s+"([^"]+)"\s*;',data.decode(),re.MULTILINE))
    return files


def named_workfile(directory, basename):
    """Retain one source per stage, preserving the source basename across moves."""
    directory.mkdir(parents=True, exist_ok=True)
    files = list(directory.glob("*.c"))
    if len(files) > 1:
        raise RuntimeError("working stage contains multiple C sources")
    destination = directory / basename
    if files and files[0] != destination:
        if destination.exists():
            raise RuntimeError("working source destination already exists")
        files[0].rename(destination)
    return destination


def cetta_units(flags_file):
    checkout = ROOT.parent / "hyperon/CeTTa"
    values = dict(line.split("=", 1) for line in flags_file.read_text().splitlines())
    compiler = shlex.split(values["CC"])
    flags = shlex.split(values["CPPFLAGS"]) + shlex.split(values["CFLAGS"])
    return checkout, [{"source": source, "compiler": compiler, "flags": flags,
                       "configuration": "BUILD=core Make variables"}
                      for source in shlex.split(values["SRC"])]


def swi_units(commands_file):
    checkout = ROOT.parent / "repos/swipl-devel"
    build = checkout / "build"
    records = []
    for line in commands_file.read_text().splitlines():
        words = shlex.split(line)
        if not words or "-c" not in words or "&&" in words:
            continue
        if Path(words[0]).name == "ccache":
            words.pop(0)
        compiler, flags, source = words[0], [], None
        i = 1
        while i < len(words):
            if words[i] in ("-o", "-MF", "-MT", "-MQ"):
                i += 2
            elif words[i] in ("-MD", "-MMD", "-MP"):
                i += 1
            elif words[i] == "-c":
                source = Path(words[i + 1]).resolve()
                i += 2
            else:
                flags.append(words[i])
                i += 1
        if source and source.suffix == ".c" and source.is_relative_to(checkout / "src"):
            records.append({"source": str(source), "compiler": [compiler], "flags": flags,
                            "configuration": "existing Ninja compile command"})
    present = {Path(row["source"]) for row in records}
    unconfigured = [str(path) for path in sorted((checkout / "src").rglob("*.c")) if path not in present]
    return build, records, unconfigured


def pycparser_check(source):
    from pycparser import c_parser
    try:
        c_parser.CParser().parse(source.decode("utf-8"), filename="translation-unit.c")
        return {"accepted": True, "diagnostic": "", "valid_result": True}
    except (c_parser.ParseError, UnicodeDecodeError) as error:
        return {"accepted": False, "diagnostic": str(error), "valid_result": True}
    except Exception as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        return {"accepted": None, "valid_result": False, "exception": type(error).__name__,
                "diagnostic": str(error),
                "frame": f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"}


def qualify(unit, cwd, output, grammar, profile, objects, cesta, metta_reader):
    record = dict(unit)
    record["build_environment"] = {"SOURCE_DATE_EPOCH": BUILD_EPOCH}
    source = Path(unit["source"])
    data = (cwd / source).read_bytes() if not source.is_absolute() else source.read_bytes()
    record["source_sha256"] = sha(data)
    native = unit["compiler"] + unit["flags"]
    # -g3 asks GCC to retain #define debugging data even with -E -P.
    # The object comparison already declares -g0; use the same debug profile here.
    record["preprocess_flags"] = ["-g0", "-E", "-P"]
    record["preprocess"], preprocessed = run(native + record["preprocess_flags"] + [str(source)], cwd)
    if not record["preprocess"]["accepted"]:
        record["source_stable"] = sha((cwd / source).read_bytes()) == record["source_sha256"]
        return record
    record["preprocessed_sha256"] = sha(preprocessed)
    baseline = named_workfile(output / "work/baseline", source.name)
    baseline.write_bytes(preprocessed)
    read = [str(cesta), "read", str(grammar), str(baseline),
            "TranslationUnit", str(profile)]
    record["reader"], wire = run(read)
    if record["reader"]["accepted"]:
        record["wire_sha256"] = sha(wire)
        wire_file = output / "work/source.csexp"
        wire_file.write_bytes(wire)
        if metta_reader:
            record["metta_data"], _ = run([str(metta_reader), str(wire_file)])
        record["writer"], printed = run([str(cesta), "write", str(grammar),
                                         str(wire_file), "TranslationUnit", str(profile)])
        if record["writer"]["accepted"]:
            emitted = named_workfile(output / "work/printed", source.name)
            emitted.write_bytes(printed)
            reread = read.copy()
            reread[3] = str(emitted)
            record["rereader"], rewire = run(reread)
            record["tree_roundtrip"] = record["rereader"]["accepted"] and rewire == wire
            record["rewire_sha256"] = sha(rewire)
    for compiler in ("gcc", "clang"):
        command = [compiler] + unit["flags"] + ["-g0", "-fsyntax-only", str(baseline)]
        record[compiler], _ = run(command, cwd)
    record["pycparser"] = pycparser_check(preprocessed)
    if objects and record.get("tree_roundtrip", False):
        record["baseline_object_source"] = "original translation unit with recorded build flags"
        for stage in ("baseline", "printed"):
            current = source if stage == "baseline" else output / "work" / stage / source.name
            object_file = output / "work" / (stage + ".o")
            record[stage + "_object"], _ = run(native + ["-g0", "-c", str(current),
                                                          "-o", str(object_file)], cwd)
            if record[stage + "_object"]["accepted"]:
                record[stage + "_object_sha256"] = sha(object_file.read_bytes())
        record["objects_equal"] = (record["baseline_object"]["accepted"] and
                                   record["printed_object"]["accepted"] and
                                   record["baseline_object_sha256"] == record["printed_object_sha256"])
    record["source_stable"] = sha((cwd / source).read_bytes()) == record["source_sha256"]
    failures = (not record.get("tree_roundtrip", False) or not record["source_stable"] or
                (objects and not record.get("objects_equal", False)) or
                (metta_reader and not record.get("metta_data", {}).get("accepted", False)))
    if failures:
        directory = output / "failures" / sha(str(source).encode())[:16]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / source.name).write_bytes(preprocessed)
        if record["reader"]["accepted"]:
            (directory / "source.csexp").write_bytes(wire)
            if record["writer"]["accepted"]:
                (directory / "printed.c").write_bytes(printed)
        (directory / "record.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", choices=("cetta", "swi"))
    parser.add_argument("--flags", type=Path, default=ROOT / "build/cetta-build-flags.txt")
    parser.add_argument("--commands", type=Path, default=ROOT / "build/swipl-build-commands.txt")
    parser.add_argument("--grammar", type=Path, default=ROOT / "build/CGNU.cf")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--cesta", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metta-reader", type=Path)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--objects", action="store_true")
    options = parser.parse_args()
    if options.corpus == "cetta":
        cwd, units = cetta_units(options.flags)
        unconfigured = []
        if options.profile is None: options.profile = ROOT / "grammar/CGNUC11.profile"
    else:
        cwd, units, unconfigured = swi_units(options.commands)
        if options.profile is None: options.profile = ROOT / "grammar/CGNU.profile"
    if options.only:
        units = [unit for unit in units if unit["source"] in options.only or
                 Path(unit["source"]).name in options.only]
    output = (options.output or (ROOT / "build/corpus" / options.corpus)).resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("corpus outputs must remain under cesta/build")
    output.mkdir(parents=True, exist_ok=True)
    # Qualification uses one immutable input image even if the development
    # binary or grammar is rebuilt while a long corpus run is in progress.
    inputs = {"cesta_sha256":sha(options.cesta.read_bytes()),
              "grammar_sha256":sha(options.grammar.read_bytes()),
              "profile_sha256":sha(options.profile.read_bytes())}
    profiles = profile_sources(options.profile)
    inputs["profile_includes"] = {name:sha(data) for name,data in profiles.items()}
    reader = options.metta_reader.resolve() if options.metta_reader else None
    if reader: inputs["metta_reader_sha256"] = sha(reader.read_bytes())
    key = sha(json.dumps(inputs,sort_keys=True).encode())[:16]
    snapshot = ROOT / "build/corpus/snapshots" / key
    snapshot.mkdir(parents=True,exist_ok=True)
    for label,path in (("cesta",options.cesta),("grammar.cf",options.grammar)):
        target = snapshot / label
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise RuntimeError("qualification snapshot hash collision")
        if not target.exists(): shutil.copy2(path,target)
    for name,data in profiles.items():
        target = snapshot / name
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError("qualification included-profile hash collision")
        if not target.exists(): target.write_bytes(data)
    if reader:
        target = snapshot / "metta-reader"
        if target.exists() and target.read_bytes() != reader.read_bytes():
            raise RuntimeError("qualification reader changed inside the selected snapshot")
        if not target.exists(): shutil.copy2(reader,target)
        reader = target
    options.cesta, options.grammar, options.profile = snapshot / "cesta",snapshot / "grammar.cf",snapshot / options.profile.name
    (snapshot / "inputs.json").write_text(json.dumps(inputs,indent=2)+"\n")
    checkout = ROOT.parent / ("hyperon/CeTTa" if options.corpus == "cetta" else "repos/swipl-devel")
    head = run(["git","rev-parse","HEAD"],checkout)[1].decode().strip()
    status = run(["git","status","--porcelain","--untracked-files=no"],checkout)[1].decode()
    qualification_script = sha(Path(__file__).read_bytes())
    records = []
    for index, unit in enumerate(units, 1):
        record = qualify(unit, cwd, output, options.grammar.resolve(), options.profile.resolve(), options.objects, options.cesta.resolve(), reader)
        records.append(record)
        summary = {"corpus": options.corpus, "units": records, "unconfigured": unconfigured,
                   "inputs": inputs,"checkout":{"head":head,"tracked_changes":status},
                   "qualification_script_sha256":qualification_script}
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"{index}/{len(units)} {unit['source']}: "
              f"read={record.get('reader', {}).get('accepted', False)} "
              f"tree={record.get('tree_roundtrip', False)} objects={record.get('objects_equal', None)}", flush=True)
        if not record.get("reader", {}).get("accepted", False):
            print(record.get("reader", record["preprocess"])["diagnostic"].strip(), flush=True)
    print(json.dumps({"units": len(records), "readers": sum(r.get("reader", {}).get("accepted", False) for r in records),
                      "tree_roundtrips": sum(r.get("tree_roundtrip", False) for r in records),
                      "identical_objects": sum(r.get("objects_equal", False) for r in records),
                      "metta_data": sum(r.get("metta_data", {}).get("accepted", False) for r in records),
                      "unconfigured": len(unconfigured)}))
    if not records or any(not r.get("tree_roundtrip",False) or not r.get("source_stable",False) or
                          (options.objects and not r.get("objects_equal",False)) or
                          (reader and not r.get("metta_data",{}).get("accepted",False)) for r in records):
        raise SystemExit("external corpus qualification failed; see the recorded stage evidence")


if __name__ == "__main__":
    main()
