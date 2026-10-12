"""Build substantial directly authored compact programs through ordinary make."""

import json
from pathlib import Path
import subprocess

from examples import PROGRAMS

ROOT = Path(__file__).resolve().parent.parent


def command(arguments, cwd=ROOT):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{arguments}: {result.stderr.decode()}")
    return result


def main():
    records = []
    output = ROOT / "build/compact-examples"
    for compiler in ("gcc", "clang"):
        destination = output / compiler
        invocation = ["make", "-s", "CC="+compiler, "BUILD="+str(destination)]
        command(invocation, ROOT / "examples/compact")
        for name, expected in PROGRAMS.items():
            result = command([destination / name])
            assert result.stdout == expected.encode() and not result.stderr
            raw = ROOT / "examples/compact" / (name+".cesta")
            ast = destination / (name+".ast.csexp")
            ast.write_bytes(command([ROOT / "build/cesta", "convert", "--from", "compact-c/2", "--to", "ast/1", raw]).stdout)
            for key in ("compact-c/1", "cmera-style/1"):
                other = destination / (name+"."+key.replace("/", "-")+".cesta")
                other.write_bytes(command([ROOT / "build/cesta", "convert", "--to", key, ast]).stdout)
                assert command([ROOT / "build/cesta", "convert", "--to", "ast/1", other]).stdout == ast.read_bytes()
            records.append({"compiler": compiler, "program": name, "output": result.stdout.decode(),
                            "ordinary_make": True, "direct_compact_source": True, "exact_style_switch": True})
        times = {name: (destination / name).stat().st_mtime_ns for name in PROGRAMS}
        stable = command(invocation, ROOT / "examples/compact")
        assert not stable.stdout and times == {name: (destination / name).stat().st_mtime_ns for name in PROGRAMS}
    (output / "summary.json").write_text(json.dumps(records, indent=2)+"\n")
    print("compact authoring: six substantial families, 12 warning-clean builds/executions, exact style switching and unchanged make passed")


if __name__ == "__main__":
    main()
