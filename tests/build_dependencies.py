"""Qualify native header dependencies and ordinary incremental mixed builds."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, cwd=ROOT):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{arguments}: {result.stderr.decode()}")
    return result.stdout


def main():
    output = ROOT / "build/build-dependencies"
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for compiler in ("gcc", "clang"):
        project = output / compiler
        project.mkdir(exist_ok=True)
        (project / "value.cesta").write_text("(unit (fn int value (void) (return FACTOR)))\n")
        (project / "main.c").write_text("int value(void);int main(void){return value()==FACTOR?0:1;}\n")
        (project / "constants.h").write_text("#define FACTOR 4\n")
        (project / "Makefile").write_text(
            ".DEFAULT_GOAL := app\nCPPFLAGS := -include constants.h\n"
            "CFLAGS := -std=c11 -O3 -g0 -Wall -Wextra -Werror -MMD -MP\n"
            f"include {ROOT}/cesta.mk\n"
            "app: main.o value.o\n\t$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@\n"
            "-include main.d value.d\n")
        command = ["make", "-s", "CC="+compiler, "CESTA="+str(BINARY)]
        run(command, project)
        run([project / "app"])
        dependencies = (project / "value.d").read_text()
        assert "value.cesta" in dependencies and "constants.h" in dependencies
        assert "cesta-generated" not in dependencies
        times = [(project / name).stat().st_mtime_ns for name in ("main.o", "value.o", "app")]
        run(command, project)
        assert times == [(project / name).stat().st_mtime_ns for name in ("main.o", "value.o", "app")]
        # The generated cache is expendable. Retain it under another name and
        # prove the dependency graph follows authored sources and headers.
        cache = project / "build/cesta-generated"
        if cache.exists():
            target = project / ("retained-cache-"+str(cache.stat().st_mtime_ns))
            cache.rename(target)
        run(command, project)
        assert times == [(project / name).stat().st_mtime_ns for name in ("main.o", "value.o", "app")]
        (project / "constants.h").write_text("#define FACTOR 7\n")
        run(command, project)
        run([project / "app"])
        assert all(before < (project / name).stat().st_mtime_ns for before, name in zip(times, ("main.o", "value.o", "app")))
        (project / "value.cesta").write_text("(unit (fn int value (void) (return (+ FACTOR 1))))\n")
        (project / "main.c").write_text("int value(void);int main(void){return value()==FACTOR+1?0:1;}\n")
        run(command, project)
        run([project / "app"])
        spaced = project / "source value.cesta"
        spaced.write_text("(unit (fn int value (void) (return FACTOR)))\n")
        run([BINARY, "cc", "--cc", compiler, "-std=c11", "-MMD", "-MP", "-MF", "spaced.d",
             "-include", "constants.h", "-c", spaced, "-o", "spaced.o"], project)
        spaced_deps = (project / "spaced.d").read_text()
        assert "source\\ value.cesta" in spaced_deps and "cesta-generated" not in spaced_deps
        for key in ("ast/1", "compact-c/1", "compact-c/2", "cmera-style/1"):
            document = project / (key.replace("/", "-")+".cesta")
            raw = project / "sealed-input.cesta"
            raw.write_text("(unit (fn int answer (void) (return 4)))\n")
            document.write_bytes(run([BINARY, "convert", "--from", "compact-c/2", "--to", key, raw], project))
            run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-c", document,
                 "-o", document.with_suffix(".o")], project)
        modes = [
            ("MM-stdout", ["-MM"], None),
            ("M-stdout", ["-M"], None),
            ("MM-output", ["-MM", "-o", "specified.dep"], "specified.dep"),
            ("MM-MF", ["-MM", "-MF", "specified.dep"], "specified.dep"),
            ("MMD-stdout", ["-c", "-MMD", "-MF", "-", "-o", "answer.o"], None),
            ("assembly", ["-S", "-MMD", "-o", "answer.s"], "answer.d"),
            ("preprocess", ["-E", "-MMD", "-o", "answer.i"], "answer.d"),
            ("link", ["-MMD", "-o", "answer"], "answer.d"),
            ("link-default", ["-MMD"], "a-answer.d" if compiler == "gcc" else "answer.d"),
        ]
        for name, flags, depfile in modes:
            directory = project / name
            directory.mkdir(exist_ok=True)
            authored = directory / "answer.cesta"
            authored.write_text('(unit (fn int main (void) (return 0)))\n')
            data = run([BINARY, "cc", "--cc", compiler, "-std=c11", *flags, "answer.cesta"], directory)
            deps = data.decode() if depfile is None else (directory / depfile).read_text()
            assert "answer.cesta" in deps and "cesta-generated" not in deps, (compiler, name, deps)
            if name.startswith("link"):
                run([directory / ("a.out" if name == "link-default" else "answer")])
        records.append({"compiler": compiler, "header_rebuild": True, "source_rebuild": True,
                        "unchanged_no_rebuild": True, "expendable_generated_cache": True,
                        "explicit_dependency_path": True, "spaced_source_dependency": True,
                        "automatic_versioned_presentation": True,
                        "native_dependency_modes": [name for name, _, _ in modes]})
    (output / "summary.json").write_text(json.dumps(records, indent=2)+"\n")
    print("build dependencies: both compilers preserve header/source rebuilds, stable builds, authored dependency paths and versioned-file dispatch")


if __name__ == "__main__":
    main()
