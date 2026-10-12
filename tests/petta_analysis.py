"""Load generated program data into PeTTa and query its concrete syntax trees."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(arguments):
    result = subprocess.run([str(x) for x in arguments], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace") or result.stdout.decode(errors="replace"))
    return result.stdout, result.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--petta", type=Path, default=ROOT.parent / "hyperon/PeTTa")
    parser.add_argument("--swipl", default="swipl")
    parser.add_argument("--output", type=Path, default=ROOT / "build/petta-analysis")
    options = parser.parse_args()
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("analysis qualification artifacts belong under build")
    output.mkdir(parents=True, exist_ok=True)
    source = output / "program.c"
    source.write_text("unsigned sum(unsigned x,unsigned y){return x+y;}"
                      "int main(void){return (int)sum(3u,4u);}\n")
    binary = ROOT / "build/cesta"
    wire, _ = run([binary, "read", "--std=c11", source])
    (output / "program.metta").write_bytes(wire)
    shutil.copyfile(ROOT / "tests/petta_analysis.metta", output / "query.metta")
    # Use PeTTa's ordinary Prolog atom spaces. Optional storage backends are
    # independent of this source-data qualification.
    command = [options.swipl, "-q", "-s", options.petta.resolve() / "src/main.pl",
               "--", output / "query.metta", "--silent"]
    stdout, stderr = run(command)
    (output / "query.stdout").write_bytes(stdout)
    (output / "query.stderr").write_bytes(stderr)
    lines = stdout.splitlines()
    serialized = [line for line in lines if line.startswith(b"(c:TranslationUnit ")]
    if len(serialized) != 1 or stdout.count("✅".encode()) != 5:
        raise RuntimeError("PeTTa did not pass every positive and negative tree query")
    restored = output / "restored.csexp"
    restored.write_bytes(serialized[0]+b"\n")
    printed, _ = run([binary, "write", "--std=c11", restored])
    replay = output / "replayed.c"
    replay.write_bytes(printed)
    rewire, _ = run([binary, "read", "--std=c11", replay])
    if rewire != wire:
        raise RuntimeError("PeTTa loading and serialization changed the generated program tree")
    version, _ = run([options.swipl, "--version"])
    summary = {"runtime":version.decode().strip(), "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
               "wire_sha256":hashlib.sha256(wire).hexdigest(), "query_checks":5,
               "functions":["sum","main"], "calls":["sum"],
               "tree_replay":True, "carrier":"ASCII source spellings; native PeTTa string carrier"}
    (output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print("PeTTa source analysis: functions, calls, variables and literal spellings queried; absence and tree replay passed")


if __name__ == "__main__":
    main()
