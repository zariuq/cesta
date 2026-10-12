#!/usr/bin/env python3
"""Compile directly authored trees with both native compilers and check output."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = {
    "unsigned-array": "10\n",
    "malloc-list": "7\n",
    "function-pointers": "13\n",
    "bitfields-designated": "10\n",
    "generic-static-assert": "11\n",
    "gnu-statement-expression": "12\n",
}


def main():
    results = []
    for compiler in ("gcc", "clang"):
        destination = ROOT / "build" / "examples" / compiler
        destination.mkdir(parents=True, exist_ok=True)
        for name, expected in PROGRAMS.items():
            profile = "CGNU" if name.startswith("gnu-") else "C11"
            output = destination / name
            command = [
                str(ROOT / "build/cesta"), "build", str(ROOT / "build" / (profile + ".cf")),
                str(ROOT / "examples" / (name + ".csexp")), "TranslationUnit",
                str(ROOT / "grammar" / (profile + ".profile")), "-o", str(output),
                "--cc", compiler, "--", "-O2", "-g0", "-Wall", "-Wextra", "-Werror",
            ]
            compiled = subprocess.run(command, text=True, capture_output=True)
            if compiled.returncode:
                sys.stderr.write(compiled.stderr)
                raise SystemExit(f"{compiler}: {name}: build failed")
            if compiled.stderr:
                raise SystemExit(f"{compiler}: {name}: unexpected diagnostic: {compiled.stderr}")
            ran = subprocess.run([str(output)], text=True, capture_output=True)
            if ran.returncode or ran.stdout != expected or ran.stderr:
                raise SystemExit(f"{compiler}: {name}: unexpected execution {ran!r}")
            results.append({"compiler": compiler, "program": name, "output": ran.stdout, "exit": ran.returncode})
    (ROOT / "build/examples/summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"direct S-expression programs: {len(PROGRAMS)} programs; {len(results)} warning-clean native builds and executions passed")
    make_results = []
    for compiler in ("gcc", "clang"):
        destination = ROOT / "build/examples" / ("ordinary-make-" + compiler)
        command = ["make", "-C", str(ROOT / "examples"), "CC=" + compiler, "BUILD=" + str(destination)]
        built = subprocess.run(command, text=True, capture_output=True)
        if built.returncode:
            sys.stderr.write(built.stdout + built.stderr)
            raise SystemExit(f"{compiler}: ordinary make failed")
        for name, expected in PROGRAMS.items():
            ran = subprocess.run([str(destination / name)], text=True, capture_output=True)
            if ran.returncode or ran.stdout != expected or ran.stderr:
                raise SystemExit(f"{compiler}: ordinary make: {name}: incorrect execution")
            make_results.append({"compiler": compiler, "program": name, "output": ran.stdout})
        incremental = subprocess.run(command, text=True, capture_output=True)
        if incremental.returncode or " cc --cc " in incremental.stdout:
            raise SystemExit(f"{compiler}: unchanged ordinary make rebuilt a program")
    (ROOT / "build/examples/make-summary.json").write_text(json.dumps(make_results, indent=2) + "\n")
    print(f"ordinary Make workflow: {len(make_results)} object builds, links and executions passed; unchanged projects need no rebuild")


if __name__ == "__main__":
    main()
