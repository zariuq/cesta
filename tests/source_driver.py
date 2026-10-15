"""Lossless documents build with original include context and make dependencies."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(arguments, cwd, expected=0):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if result.returncode != expected:
        raise RuntimeError(str(arguments) + "\n" + result.stderr.decode(errors="replace"))
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta", type=Path, default=ROOT / "build/cesta")
    binary = parser.parse_args().cesta.resolve()
    comparisons = 0
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        work = Path(temporary)
        text = b'''/* Preserve columns, comments and source-line builtins. */
#include "local.h"
#include "empty.h"
#include <limits.h>
#define BUMP(x) ((x) + VALUE)
#if ENABLED
int f(void) { return BUMP(__LINE__); }
#else
int f(void) { return 0; }
#endif
const char *name = __FILE__;
const char *base = __BASE_FILE__;
const char *leaf = __FILE_NAME__;
#ifndef REENTERED
#define REENTERED
#include "unit.cesta"
#else
int self_included = 1;
#endif
'''
        # Native and Cesta inputs have the same spelling and basename in
        # independent directories, so __FILE__ and object file names agree.
        # The guarded self-include reads the supplied document contents again.
        text = text.replace(b'/* Preserve', b'#ifndef REENTERED\n/* Preserve', 1)
        text = text.replace(b'#ifndef REENTERED\n#define REENTERED', b'#define REENTERED', 1)
        expected_value = next(i for i, line in enumerate(text.splitlines(), 1)
                              if line.startswith(b"int f(void) { return BUMP")) + 3
        for compiler in ("gcc", "clang"):
            native, document = work / (compiler + "-native"), work / (compiler + "-document")
            for directory in (native, document):
                directory.mkdir()
                (directory / "local.h").write_text("#define VALUE 3\n")
                (directory / "empty.h").write_text("/* still a build dependency */\n")
                (directory / "unit.cesta").write_bytes(text)
            encoded = run([binary, "source", "unit.cesta"], native)
            (document / "unit.cesta").write_bytes(encoded)
            if run([binary, "source-bytes", "unit.cesta"], document) != text:
                raise RuntimeError("source authoring bytes changed")
            flags = ["-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror", "-DENABLED=1"]
            run([compiler, *flags, "-x", "c", "-c", "unit.cesta", "-o", "unit.o"], native)
            run([binary, "cc", "--cc", compiler, *flags, "-MMD", "-MP", "-c", "unit.cesta", "-o", "unit.o"], document)
            if (native / "unit.o").read_bytes() != (document / "unit.o").read_bytes():
                raise RuntimeError(compiler + ": source document changed native objects")
            dependency = (document / "unit.d").read_text()
            if any(name not in dependency for name in ("unit.cesta", "local.h", "empty.h")) or "limits.h" in dependency:
                raise RuntimeError("MMD lost actual user dependencies or kept system headers")
            if "cesta-generated" in dependency:
                raise RuntimeError("generated paths escaped into make dependencies")
            for mode in ("-M", "-MM"):
                expected = run([compiler, *flags, "-x", "c", mode, "-MQ", "target space.o", "unit.cesta"], native)
                actual = run([binary, "cc", "--cc", compiler, *flags, mode, "-MQ", "target space.o", "unit.cesta"], document)
                if set(expected.replace(b"\\\n", b" ").split()) != set(actual.split()):
                    raise RuntimeError(compiler + ": dependency-only output changed: " + repr(expected) + " vs " + repr(actual))
            expected = run([compiler, *flags, "-x", "c", "-E", "-P", "unit.cesta"], native)
            actual = run([binary, "cc", "--cc", compiler, *flags, "-E", "-P", "unit.cesta"], document)
            # Whitespace can differ, but tokens and embedded string bytes must
            # agree; the stronger object comparison above checks the latter.
            if expected.split() != actual.split():
                raise RuntimeError(compiler + ": preprocessing-only output changed")
            (document / "main.c").write_text(f"extern int f(void); int main(void){{ return f()=={expected_value}?0:1; }}\n")
            run([binary, "cc", "--cc", compiler, *flags, "main.c", "unit.cesta", "-o", "mixed"], document)
            run(["./mixed"], document)
            (document / "tiny.cesta").write_text("(unit (fn int extra () (return 2)))\n")
            (document / "other").mkdir()
            (document / "other/local.h").write_text("#define VALUE 17\n")
            other = document / "other/other.c"
            other.write_text('#include "local.h"\nint other(void){return VALUE;}\n')
            (document / "other/other.cesta").write_bytes(run([binary, "source", "other/other.c"], document))
            (document / "joined.c").write_text("extern int f(void),extra(void),other(void);"
                + f"int main(void){{return f()=={expected_value}&&extra()==2&&other()==17?0:1;}}\n")
            run([binary, "cc", "--cc", compiler, *flags, "joined.c", "unit.cesta", "tiny.cesta",
                 "other/other.cesta", "-o", "joined"], document)
            run(["./joined"], document)
            # Different simultaneous configurations must not overwrite each
            # other's preprocessed inputs in the generated-source cache.
            jobs = []
            for active in (0, 1, 0, 1):
                output = "parallel-" + str(len(jobs)) + ".o"
                arguments = [binary, "cc", "--cc", compiler, *flags[:-1], "-DENABLED="+str(active),
                             "-c", "unit.cesta", "-o", output]
                jobs.append((active, output, subprocess.Popen(list(map(str, arguments)), cwd=document,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
            for active, output, process in jobs:
                stdout, stderr = process.communicate()
                if process.returncode:
                    raise RuntimeError("parallel source build failed: " + stderr.decode())
                run([compiler, *flags[:-1], "-DENABLED="+str(active), "-x", "c", "-c", "unit.cesta",
                     "-o", "reference-"+output], native)
                if (document / output).read_bytes() != (native / ("reference-"+output)).read_bytes():
                    raise RuntimeError("simultaneous macro configurations changed objects")
            bad = document / "invalid.cesta"
            bad.write_text('(cesta source/10 "int injected;")\n')
            refusal = subprocess.run([str(binary), "cc", "--cc", compiler, "-c", "invalid.cesta"],
                                     cwd=document, capture_output=True)
            if not refusal.returncode or b"source/1" not in refusal.stderr:
                raise RuntimeError("source compilation accepted an unknown document version")
            # Plain make remains the authoring workflow, including header
            # invalidation and an unchanged second build.
            (document / "Makefile").write_text(".DEFAULT_GOAL := app\n"
                + "CFLAGS := " + " ".join(flags) + " -MMD -MP\n"
                + f"include {ROOT}/cesta.mk\n"
                + "app: main.o unit.o\n\t$(CC) $^ -o $@\n-include main.d unit.d\n")
            run(["make", "-s", "CC=" + compiler, "CESTA=" + str(binary)], document)
            run(["./app"], document)
            before = (document / "unit.o").stat().st_mtime_ns
            run(["make", "-s", "CC=" + compiler, "CESTA=" + str(binary)], document)
            if (document / "unit.o").stat().st_mtime_ns != before:
                raise RuntimeError("unchanged make rebuilt the source document")
            stamp = max((document / "unit.o").stat().st_mtime_ns, (document / "app").stat().st_mtime_ns) + 1_000_000_000
            os.utime(document / "empty.h", ns=(stamp, stamp))
            run(["make", "-s", "CC=" + compiler, "CESTA=" + str(binary)], document)
            if (document / "unit.o").stat().st_mtime_ns == before:
                raise RuntimeError("empty included header did not invalidate the object")
            comparisons += 1
    print(f"source driver: {comparisons} exact native objects; self/empty/system includes; "
          "preprocessing and dependencies; mixed source/compact/C links; parallel configurations; incremental make")


if __name__ == "__main__":
    main()
