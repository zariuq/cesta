"""Build the production tool with both compilers at both optimization levels.

The Makefile supplies the actual production compile command. Each independently
built binary must preprocess and elaborate the same macro-containing source.
"""

import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(arguments):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(" ".join(map(str, arguments)) + "\n" +
                           result.stderr.decode(errors="replace")[-6000:])
    return result.stdout


def main():
    work = ROOT / "build/compiler-builds"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "portable.c"
    source.write_text("/* source and expansion are both exercised */\n"
                      "#define TWICE(x) ((x) + (x))\n"
                      "_Static_assert(TWICE(3) == 6, \"expansion\");\n"
                      "unsigned f(unsigned x) { return TWICE(x); }\n")
    results = {}
    for compiler in ("gcc", "clang"):
        for level in ("0", "2"):
            commands = run(["make", "-s", "-n", "-B", "build/cesta",
                            "CC=" + compiler, "CFLAGS=-O" + level + " -g0"])
            production = [shlex.split(line) for line in commands.decode().splitlines()
                          if "-DCE_BUILTIN_LANGUAGES" in line]
            if len(production) != 1:
                raise RuntimeError("expected one production compile command")
            arguments = production[0]
            binary = work / (compiler + "-O" + level)
            arguments[arguments.index("-o") + 1] = str(binary)
            run(arguments)
            result = run([str(binary), "types", "--cc", compiler,
                          "-std=gnu11", str(source)])
            if b"(types" not in result or b"(from \"TWICE\"" not in result:
                raise RuntimeError("portable build did not retain typed macro provenance")
            results[compiler, level] = result
            print(compiler, "-O" + level, "production build and typed provenance pass", flush=True)
    for compiler in ("gcc", "clang"):
        if results[compiler, "0"] != results[compiler, "2"]:
            raise RuntimeError(compiler + ": optimization changed the tool's analysis")


if __name__ == "__main__":
    main()
