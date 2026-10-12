"""Replay a byte-safe transport through the actual three host readers."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from corpus import ROOT


def run(arguments, *, environment=None):
    result = subprocess.run([str(arg) for arg in arguments], cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise RuntimeError(f"{arguments}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--he-python", type=Path)
    parser.add_argument("--he", type=Path, default=ROOT.parent / "hyperon/hyperon-experimental")
    parser.add_argument("--petta", type=Path, default=ROOT.parent / "hyperon/PeTTa")
    options = parser.parse_args()
    if options.he_python is None:
        installations = sorted((ROOT / "build/oracles/python311").glob("*/bin/python3.11"))
        if len(installations) != 1:
            raise RuntimeError("supply the Python interpreter matching the existing HE extension")
        options.he_python = installations[0]
    output = ROOT / "build/portable-readers"
    output.mkdir(parents=True, exist_ok=True)
    run([ROOT / "build/test-wire"])
    run([ROOT / "build/cesta", "unpack", ROOT / "build/wire-transport.metta"])
    original = (ROOT / "build/wire-original.atom").read_bytes()
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(options.he / "python")
    commands = {
        "CeTTa": [ROOT / "build/cetta-data-reader", ROOT / "build/wire-transport.metta", "--dump"],
        "PeTTa": ["swipl", "-q", "-s", ROOT / "tests/petta_reader_bridge.pl", "--",
                  options.petta / "src/parser.pl", ROOT / "build/wire-transport.metta"],
        "HE": [options.he_python, ROOT / "tests/he_reader_bridge.py", ROOT / "build/wire-transport.metta"]
    }
    records = []
    for name, command in commands.items():
        serialized = run(command, environment=environment)
        path = output / (name+".wire")
        path.write_bytes(serialized)
        restored = run([ROOT / "build/cesta", "unpack", path])
        if restored != original:
            raise RuntimeError(name+" changed an atom kind, byte spelling or ordered subtree")
        records.append({"reader": name, "exact_atom_replay": True,
            "serialized_sha256": hashlib.sha256(serialized).hexdigest()})
    # Verify that a transported presentation can still compile without a host
    # reader or a Cesta runtime at program execution.
    source = output / "answer.c"
    source.write_text("int answer(void){return 7;}\n")
    ast = run([ROOT / "build/cesta", "read", "--std=c11", source])
    wire_program = output / "answer.cesta"
    wire_program.write_bytes(run([ROOT / "build/cesta", "view", "--std=c11", "--as", "compact-c/2",
                                 "--wire=portable/1", source]))
    assert run([ROOT / "build/cesta", "write", "--std=c11", wire_program])
    exact = output / "answer-exact.csexp"
    exact.write_bytes(run([ROOT / "build/cesta", "convert", "--std=c11", "--to", "ast/1", wire_program]))
    printed = output / "answer-printed.c"
    printed.write_bytes(run([ROOT / "build/cesta", "write", "--std=c11", exact]))
    assert run([ROOT / "build/cesta", "read", "--std=c11", printed]) == ast
    for compiler in ("gcc", "clang"):
        baseline, translated = output / (compiler+"-native.o"), output / (compiler+"-wire.o")
        flags = ["-std=c11", "-O3", "-g0", "-Wall", "-Wextra", "-Werror", "-c"]
        run([compiler, *flags, source, "-o", baseline])
        run([ROOT / "build/cesta", "cc", "--cc", compiler, *flags, wire_program, "-o", translated])
        assert baseline.read_bytes() == translated.read_bytes()
    summary = {"format": "cesta-wire/1", "records": records,
        "every_byte": 256, "controls": ["PI", "EXP", "True", "False", "001", "1u", "1.0", "+", "λ"],
        "program_tree_replay": True, "matching_objects": ["gcc", "clang"],
        "boundary": "Portable quoted transport uses ordinary host reader tokenizers; presentation payload metrics are measured before transport."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print("portable readers: CeTTa, PeTTa and ordinary HE tokenizer preserve all byte values, atom kinds, reserved names, numeric spellings and Unicode; both compiler objects match")


if __name__ == "__main__":
    main()
