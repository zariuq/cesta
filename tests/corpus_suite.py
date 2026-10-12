"""Full corpus gate; reuse object evidence only while all measured inputs agree."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pycparser
from corpus import ROOT, cetta_units, profile_sources, run, sha, swi_units


def command(arguments):
    result = subprocess.run([str(item) for item in arguments],cwd=ROOT,check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def tool_inputs():
    result = {}
    for name in ("gcc","clang","cc","as"):
        executable = Path(shutil.which(name)).resolve()
        result[name] = {"sha256":sha(executable.read_bytes()),"version":run([name,"--version"])[1].decode().splitlines()[0]}
    cc1 = Path(run(["gcc","-print-prog-name=cc1"])[1].decode().strip()).resolve()
    result["gcc-cc1"] = sha(cc1.read_bytes())
    result["pycparser"] = {"version":pycparser.__version__,
        "sources":{path.name:sha(path.read_bytes()) for path in Path(pycparser.__file__).parent.glob("*.py")}}
    return result


def fresh(summary_path,manifest_path,corpus,units,cwd,tools):
    if not summary_path.exists() or not manifest_path.exists():
        return False
    summary = json.loads(summary_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    grammar = ROOT / "build/CGNU.cf"
    profile = ROOT / ("grammar/CGNUC11.profile" if corpus == "cetta" else "grammar/CGNU.profile")
    inputs = {"cesta_sha256":sha((ROOT / "build/cesta").read_bytes()),
              "grammar_sha256":sha(grammar.read_bytes()),"profile_sha256":sha(profile.read_bytes()),
              "profile_includes":{name:sha(data) for name,data in profile_sources(profile).items()},
              "metta_reader_sha256":sha((ROOT / "build/cetta-data-reader").read_bytes())}
    if (summary["inputs"] != inputs or manifest["tools"] != tools or
        summary["qualification_script_sha256"] != sha((ROOT / "tests/corpus.py").read_bytes()) or
        len(units) != len(summary["units"])):
        return False
    for unit,record in zip(units,summary["units"]):
        if any(unit[key] != record[key] for key in ("source","compiler","flags","configuration")):
            return False
        if not all(record.get(key,False) for key in ("tree_roundtrip","objects_equal","source_stable")):
            return False
        if not record.get("metta_data",{}).get("accepted",False):
            return False
        source = Path(unit["source"])
        if sha((cwd / source).read_bytes()) != record["source_sha256"]:
            return False
        result,data = run(unit["compiler"]+unit["flags"]+record["preprocess_flags"]+[str(source)],cwd)
        if not result["accepted"] or sha(data) != record["preprocessed_sha256"]:
            return False
    print(f"{corpus}: {len(units)} source/build/preprocessor/tool inputs match the qualified object/tree/MeTTa evidence",flush=True)
    return True


def main():
    command([sys.executable,"-B","tests/corpus_setup.py"])
    tools = tool_inputs()
    summaries = []
    for corpus in ("cetta","swi"):
        if corpus == "cetta":
            cwd,units = cetta_units(ROOT / "build/cetta-build-flags.txt")
        else:
            cwd,units,_ = swi_units(ROOT / "build/swipl-build-commands.txt")
        output = ROOT / "build/corpus" / ("qualified-"+corpus)
        summary,manifest = output / "summary.json",output / "inputs-manifest.json"
        if not fresh(summary,manifest,corpus,units,cwd,tools):
            command([sys.executable,"-B","tests/corpus.py",corpus,"--objects",
                     "--metta-reader","build/cetta-data-reader","--output",output])
            if tools != tool_inputs():
                raise RuntimeError("compiler/oracle tools changed during corpus qualification")
            manifest.write_text(json.dumps({"tools":tools},indent=2)+"\n")
        summaries.append(summary)
    command([sys.executable,"-B","tests/corpus_inventory.py"])
    command([sys.executable,"-B","tests/corpus_oracles.py",*summaries])
    command([sys.executable,"-B","tests/gnu_inventory.py",*summaries])
    print("corpus suite: every configured object/tree/MeTTa gate passed; zero unexplained oracle differences",flush=True)


if __name__ == "__main__":
    main()
