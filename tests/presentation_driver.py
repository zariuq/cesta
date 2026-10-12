"""Exercise the native presentation/build boundary with independently authored code."""

import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, *, cwd=ROOT, expected=0):
    result = subprocess.run([str(argument) for argument in arguments], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expected:
        raise RuntimeError(f"{arguments}: status {result.returncode}: {result.stderr.decode()}")
    return result


def nonlayout(data):
    quoted, escaped, size = False, False, 0
    for byte in data:
        if quoted:
            size += 1
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte not in b" \t\r\n":
            size += 1
            quoted = byte == 34
    return size


def main():
    output = ROOT / "build/presentation-driver"
    output.mkdir(parents=True, exist_ok=True)
    native = output / "native"
    native.mkdir(exist_ok=True)
    source = native / "sum.c"
    source.write_text("unsigned sum(unsigned x,unsigned y){return x+y;}\n")
    ast = output / "sum.csexp"
    ast.write_bytes(run([BINARY, "read", "--std=c11", source]).stdout)
    compact = output / "sum.cesta"
    viewed = run([BINARY, "view", "--std=c11", "--as", "compact-c/2", "--stats", ast])
    compact.write_bytes(viewed.stdout)
    assert b"covered by short forms" in viewed.stderr
    exact = output / "ast.csexp"
    exact.write_bytes(run([BINARY, "convert", "--std=c11", "--to", "ast/1", compact]).stdout)
    assert run([BINARY, "convert", "--std=c11", "--to", "compact-c/2", exact]).stdout == compact.read_bytes()
    assert run([BINARY, "view", "--std=c11", "--as", "compact-c/2", source]).stdout == compact.read_bytes()
    earlier = output / "compact-1.cesta"
    earlier.write_bytes(run([BINARY, "convert", "--std=c11", "--to", "compact-c/1", compact]).stdout)
    assert run([BINARY, "convert", "--std=c11", "--to", "compact-c/2", earlier]).stdout == compact.read_bytes()
    foreign = output / "cmera.cesta"
    foreign.write_bytes(run([BINARY, "convert", "--std=c11", "--to", "cmera-style/1", compact]).stdout)
    assert b"(function sum" in foreign.read_bytes() and b" -> unsigned " in foreign.read_bytes()
    assert run([BINARY, "convert", "--std=c11", "--to", "compact-c/2", foreign]).stdout == compact.read_bytes()
    assert run([BINARY, "convert", "--std=c11", "--to", "ast/1", foreign]).stdout == exact.read_bytes()
    metrics_path = output / "metrics.json"
    measured = run([BINARY, "view", "--std=c11", "--as", "compact-c/2", "--stats-json", metrics_path, source])
    metrics = json.loads(metrics_path.read_text())
    assert measured.stdout == compact.read_bytes()
    assert metrics["payload_bytes"]+metrics["envelope_bytes"] == nonlayout(measured.stdout)
    assert metrics["C_payload_bytes"] == len(re.sub(rb'\s+', b'', source.read_bytes()))
    assert sum(row["occurrences"] for row in metrics["by_constructor"]) == metrics["constructors"]
    assert sum(row["shortened"] for row in metrics["by_constructor"]) == metrics["shortened"]
    printed = output / "printed.c"
    printed.write_bytes(run([BINARY, "write", "--std=c11", compact]).stdout)
    assert run([BINARY, "read", "--std=c11", printed]).stdout == ast.read_bytes()
    raw = output / "authored.cesta"
    raw.write_text("(unit (fn unsigned sum ((unsigned x) (unsigned y)) (return (+ x y))))\n")
    authored_exact = output / "authored-exact.csexp"
    authored_exact.write_bytes(run([BINARY, "convert", "--std=c11", "--from", "compact-c/2", "--to", "ast/1", raw]).stdout)
    assert authored_exact.read_bytes() == exact.read_bytes()
    for name, data in {
        "wrong-version": viewed.stdout.replace(b"compact-c/2", b"compact-c/9", 1),
        "relabeled-version": viewed.stdout.replace(b"compact-c/2", b"compact-c/1", 1),
        "wrong-schema": re.sub(rb'"[0-9a-f]{16}"', b'"0000000000000000"', viewed.stdout, count=1),
        "wrong-rules": re.sub(rb'("[0-9a-f]{16}"\s+)"[0-9a-f]{16}"', rb'\1"0000000000000000"', viewed.stdout, count=1),
    }.items():
        bad = output / (name + ".cesta")
        bad.write_bytes(data)
        assert run([BINARY, "write", "--std=c11", bad], expected=1).stderr
    project = output / "project"
    project.mkdir(exist_ok=True)
    (project / "sum.cesta").write_bytes(raw.read_bytes())
    (project / "main.c").write_text("unsigned sum(unsigned,unsigned);int main(void){return sum(3u,4u)==7u?0:1;}\n")
    (project / "Makefile").write_text(".DEFAULT_GOAL := app\nCFLAGS := -std=c11 -O3 -g0 -Wall -Wextra -Werror\n"
                                     f"include {ROOT}/cesta.mk\napp: main.o sum.o\n\t$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@\n")
    records = []
    for compiler in ("gcc", "clang"):
        flags = ["-std=c11", "-O3", "-g0", "-Wall", "-Wextra", "-Werror"]
        original = output / (compiler + "-original.o")
        translated = output / (compiler + "-translated.o")
        run([compiler, *flags, "-c", source, "-o", original])
        run([BINARY, "cc", "--cc", compiler, *flags, "-c", compact, "-o", translated])
        assert original.read_bytes() == translated.read_bytes()
        # Independent authoring is checked at the tree boundary above. Its
        # generated basename differs intentionally, so compare executable behavior.
        authored = output / (compiler + "-authored.o")
        run([BINARY, "cc", "--cc", compiler, *flags, "-c", raw, "-o", authored])
        executable = output / (compiler + "-authored")
        run([BINARY, "cc", "--cc", compiler, *flags, authored, project / "main.c", "-o", executable])
        run([executable])
        # Separate build directories exercise both compilers without deleting
        # products or depending on timestamps of a previous compiler's objects.
        compiler_project = project / compiler
        compiler_project.mkdir(exist_ok=True)
        for filename in ("sum.cesta", "main.c", "Makefile"):
            (compiler_project / filename).write_bytes((project / filename).read_bytes())
        run(["make", "-s", "CC="+compiler, "CESTA="+str(BINARY)], cwd=compiler_project)
        run([compiler_project / "app"])
        mtimes = [(compiler_project / name).stat().st_mtime_ns for name in ("sum.o", "main.o", "app")]
        run(["make", "-s", "CC="+compiler, "CESTA="+str(BINARY)], cwd=compiler_project)
        assert mtimes == [(compiler_project / name).stat().st_mtime_ns for name in ("sum.o", "main.o", "app")]
        records.append({"compiler": compiler, "object_sha256": hashlib.sha256(translated.read_bytes()).hexdigest(),
                        "independent_authoring": True, "mixed_make": True, "unchanged_rebuild": False})
    (output / "summary.json").write_text(json.dumps({"records": records, "exact_switching": True,
                                                     "version_schema_rule_refusals": True}, indent=2) + "\n")
    print("presentation driver: exact switching, direct authoring, version/schema/rule refusals, matching objects and ordinary mixed make passed in both compilers")


if __name__ == "__main__":
    main()
