"""cesta build: a declared project compiles and links as one language."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/project-build-tests"


def run(args, cwd, success=True):
    result = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{args}\nstatus {result.returncode}\n{result.stderr.decode(errors='replace')}")
    return result


def summary(stderr, target):
    m = re.search(rf"cesta build: {target}: (\d+) compiled, (\d+) up to date; (\w+)", stderr.decode())
    assert m, stderr
    return int(m.group(1)), int(m.group(2)), m.group(3)


PROJECT = """(cesta project/1
  (compiler gcc)
  (out "out")
  (profile hosted (flags "-std=gnu11" "-O2" "-g" "-Wall" "-Werror"))
  (profile freestanding
    (flags "-std=gnu11" "-O2" "-ffreestanding" "-fPIE" "-fno-stack-protector" "-mno-red-zone" "-mgeneral-regs-only")
    (link "-nostdlib" "-static-pie"))
  (target values (profile hosted) (kind archive) (sources "lib/*.cesta") (include "include"))
  (target app (profile hosted) (sources "src/*.cesta" "src/*.c" "src/*.S") (include "include")
    (define "GREETING=\\"hello\\"") (link "out/values/libvalues.a"))
  (target bare (profile freestanding) (sources "bare/*.cesta")))
"""

FILES = {
    "include/values.h": "#define BASE 40\nint value(void);\n",
    "lib/value.cesta": '(cesta native-c/1 compact-c/2 (unit (include "values.h") (fn int value (void) (return (+ BASE 1)))))\n',
    "src/main.cesta": "(cesta native-c/1 compact-c/2\n  (unit\n    (include <stdio.h>) (include \"values.h\")\n"
                      "    (var int (function twice (int x)))\n    (var int (function one void))\n"
                      "    (fn int main (void)\n      (call printf \"%s %d\\n\" GREETING (+ (call value) (call twice (call one))))\n"
                      "      (return 0))))\n",
    "src/twice.cesta": "(unit (fn int twice ((int x)) (return (+ x x))))\n",
    "src/plain.c": "int unused_plain(void) { return 7; }\n",
    "src/one.S": ".globl one\none:\n  movl $1, %eax\n  ret\n",
    # A static PIE with its own entry: exits through the syscall with table[2];
    # the pointer keeps a relative relocation in the image.
    "bare/start.cesta": r'(unit (var (static int) ([] table 3) (init 1 2 3)) '
                        r'(var int (* (const) pointer) (& ([] table 2))) '
                        r'(fn void _start (void) (asm-input __asm__ (volatile) ("\"mov $60, %%eax\\n\\tsyscall\"") () '
                        r'("\"D\"" ([] table 2))) (while 1 (block))))' "\n",
}


def dependency_controls(compiler, project):
    """Each dependency changes independently; a shared header cannot mask it."""
    for directory in ("lib", "src"):
        (project / directory).mkdir(parents=True)
    header = project / "api #$ value.h"
    header.write_text("#define BASE 11\nint value(void);\n")
    library = project / "lib/value file.c"
    library.write_text('#include "api #$ value.h"\nint value(void) { return BASE; }\n')
    (project / "src/main file.c").write_text('#include "api #$ value.h"\nint main(void) { return value(); }\n')
    linker = project / "layout.ld"
    linker.write_text("SECTIONS { .review : { BYTE(17) } } INSERT AFTER .data;\n")
    (project / "policy.dat").write_text("first\n")
    text = '''(cesta project/1 (out "out")
      (profile hosted (flags "-std=c11" "-O0") (link "-Wl,-T,layout.ld" "-Wl,-rpath,$ORIGIN"))
      (target app (profile hosted) (sources "src/main file.c") (include ".")
        (depends values) (link-inputs "policy.dat") (link "./out/values/libvalues.a"))
      (target values (profile hosted) (kind archive) (sources "lib/value file.c") (include ".")))
'''
    manifest = project / "cesta.project"
    manifest.write_text(text)
    build = [BINARY, "build", "--cc", compiler, "-j", "4", "app"]

    def exit_value(path):
        return subprocess.run([path], cwd=project).returncode

    def section(path):
        return run(["readelf", "-x", ".review", path], project).stdout

    result = run(build, project)
    assert summary(result.stderr, "values")[2] == "archived"
    assert summary(result.stderr, "app")[2] == "linked"
    executable = project / "out/app/app"
    assert exit_value(executable) == 11
    first_section = section(executable)

    library.write_text('#include "api #$ value.h"\nint value(void) { return BASE + 1; }\n')
    result = run(build, project)
    assert summary(result.stderr, "app") == (0, 1, "linked"), result.stderr
    assert exit_value(executable) == 12
    result = run(build, project)
    assert summary(result.stderr, "app") == (0, 1, "current"), result.stderr

    linker.write_text("SECTIONS { .review : { BYTE(34) } } INSERT AFTER .data;\n")
    result = run(build, project)
    assert summary(result.stderr, "app") == (0, 1, "linked"), result.stderr
    assert section(executable) != first_section
    (project / "policy.dat").write_text("second\n")
    result = run(build, project)
    assert summary(result.stderr, "app") == (0, 1, "linked"), result.stderr

    exported = project / "exported"
    run([*build, "--export-c", "exported"], project)
    assert (exported / "api #$ value.h").exists()
    assert not (exported / "exported").exists(), "include '.' copied the export recursively"
    assert not (exported / "out/app/app").exists(), "export copied a prebuilt application"
    make = ["make", "-s", "-j4", "-C", exported, "CC=" + compiler]
    run(make, project)
    app = exported / "out/app/app"
    assert exit_value(app) == 12
    before = app.stat().st_mtime_ns
    run(make, project)
    assert app.stat().st_mtime_ns == before, "an unchanged export relinked"
    (exported / header.name).write_text("#define BASE 21\nint value(void);\n")
    run(make, project)
    assert exit_value(app) == 22, "exported header change did not rebuild"
    section_before = section(app)
    (exported / "layout.ld").write_text("SECTIONS { .review : { BYTE(51) } } INSERT AFTER .data;\n")
    run(make, project)
    assert section(app) != section_before, "exported linker-script change did not relink"

    for mutation, message in (
        (text.replace("(depends values)", "(depends absent)"), b"undeclared target"),
        (text.replace("(kind archive)", "(kind archive) (depends app)"), b"dependency cycle"),
        (text.replace("(kind archive)", '(kind archive) (output "./out/app/app")'), b"distinct output"),
    ):
        manifest.write_text(mutation)
        result = run(build, project, success=False)
        assert message in result.stderr, result.stderr
    manifest.write_text(text)
    print(compiler + ": library-only edits, forward dependencies, selected closure, linker inputs, special header names and exported incremental rebuilds passed", flush=True)


def freestanding_macro(compiler, project):
    """Link-only PIE options must not leak into macro-name preprocessing."""
    project.mkdir(parents=True)
    source = project / "macro.cesta"
    module = ROOT / "macros/Narrowing.module"
    source.write_text('(cesta native-c/1 compact-c/2 (unit (include <stdint.h>) '
        '(use-macros ' + json.dumps(str(module)) + ' Checked) '
        '(fn void _start (void) (var uint32_t output 0u) '
        '(var int accepted (try-narrow uint64_t uint32_t (cast uint64_t 3ull) output)) '
        '(var long status (?: (&& accepted (== output 3u)) 0L 99L)) '
        '(native ' + json.dumps('__asm__ volatile ("syscall" : : "a"(60L), "D"(status) : "rcx", "r11", "memory"); for (;;) {}') + '))))\n')
    executable = project / "macro"
    run([BINARY, "cc", "--cc", compiler, "-std=gnu11", "-O2", "-ffreestanding", "-fPIE",
         "-fno-stack-protector", "-mno-red-zone", "-mgeneral-regs-only", "-Wall", "-Wextra", "-Werror",
         "-nostdlib", "-static-pie", source, "-o", executable], project)
    run([executable], project)
    print(compiler + ": freestanding static PIE with typed macro preprocessing passed", flush=True)


def response_controls(compiler, project):
    """An edited command file invalidates native and exported objects."""
    project.mkdir(parents=True)
    (project / "main.c").write_text("int value(void);int main(void){return value();}\n")
    (project / "value.c").write_text("int value(void){return VALUE;}\n")
    (project / "outer.rsp").write_text('"@inner flags.rsp"\n')
    (project / "inner flags.rsp").write_text("-std=c11 -DVALUE=3\n")
    (project / "link.rsp").write_text('"@inner link.rsp"\n')
    (project / "inner link.rsp").write_text("out/values/libvalues.a -Wl,-T,layout.ld\n")
    (project / "layout.ld").write_text("SECTIONS { .response : { BYTE(34) } } INSERT AFTER .data;\n")
    (project / "cesta.project").write_text('''(cesta project/1 (out "out")
      (profile local (flags "@outer.rsp") (link "@link.rsp"))
      (profile unused (flags "@absent.rsp"))
      (target app (profile local) (sources "main.c"))
      (target values (profile local) (kind archive) (sources "value.c"))
      (target not_selected (profile unused) (sources "value.c")))''')
    command = [BINARY, "build", "--cc", compiler, "-j4", "app"]
    run(command, project)
    executable = project / "out/app/app"
    assert subprocess.run([executable]).returncode == 3
    (project / "inner flags.rsp").write_text("-std=c11 -DVALUE=7\n")
    run(command, project)
    assert subprocess.run([executable]).returncode == 7
    object_file = project / "out/app/obj/main.c.o"
    compiled = object_file.stat().st_mtime_ns
    run(command, project)
    assert object_file.stat().st_mtime_ns == compiled
    section = run(["readelf", "-x", ".response", executable], project).stdout
    (project / "layout.ld").write_text("SECTIONS { .response : { BYTE(99) } } INSERT AFTER .data;\n")
    run(command, project)
    assert object_file.stat().st_mtime_ns == compiled
    assert section != run(["readelf", "-x", ".response", executable], project).stdout
    run([*command, "--export-c", "exported"], project)
    exported = project / "exported"
    for name in ("outer.rsp", "inner flags.rsp", "link.rsp", "inner link.rsp", "layout.ld"):
        assert (exported / name).exists(), name
    make = ["make", "-s", "-j4", "CC=" + compiler]
    run(make, exported)
    assert subprocess.run([exported / "out/app/app"]).returncode == 7
    (exported / "inner flags.rsp").write_text("-std=c11 -DVALUE=11\n")
    run(make, exported)
    assert subprocess.run([exported / "out/app/app"]).returncode == 11
    (project / "inner flags.rsp").rename(project / "retained-inner.rsp")
    refused = run(command, project, success=False)
    assert b"inner flags.rsp" in refused.stderr
    (project / "retained-inner.rsp").rename(project / "inner flags.rsp")
    (project / "outer.rsp").write_text('"@outer.rsp"\n')
    refused = run(command, project, success=False)
    assert b"recursive compiler response file" in refused.stderr
    print(compiler + ": nested response dependencies, inferred archive edges, exports, missing inputs and cycles passed", flush=True)


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    records = []
    for compiler in ("gcc", "clang"):
        project = WORK / compiler
        for name, text in FILES.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text)
        (project / "cesta.project").write_text(PROJECT)
        build = [BINARY, "build", "--cc", compiler, "-j", "4"]

        result = run(build, project)
        assert summary(result.stderr, "values")[2] == "archived", result.stderr
        compiled, _, state = summary(result.stderr, "app")
        assert compiled == 4 and state == "linked", result.stderr
        assert run([project / "out/app/app"], project).stdout == b"hello 43\n"
        # The freestanding target exits with its own syscall, and its static PIE
        # needs only relative relocations.
        status = subprocess.run([project / "out/bare/bare"], cwd=project).returncode
        assert status == 3, status
        relocations = run(["readelf", "-rW", project / "out/bare/bare"], project).stdout.decode()
        kinds = set(re.findall(r"R_X86_64_\w+", relocations))
        assert kinds <= {"R_X86_64_RELATIVE"}, kinds

        # Nothing changed: nothing compiles, nothing links.
        result = run(build, project)
        assert summary(result.stderr, "app") == (0, 4, "current"), result.stderr
        # A header change rebuilds the units that include it, and only those.
        time.sleep(0.01)
        (project / "include/values.h").write_text("#define BASE 50\nint value(void);\n")
        result = run(build, project)
        assert summary(result.stderr, "values")[0] == 1 and summary(result.stderr, "app")[0] == 1, result.stderr
        assert run([project / "out/app/app"], project).stdout == b"hello 53\n"
        # A changed command rebuilds the target's units.
        (project / "cesta.project").write_text(PROJECT.replace('"GREETING=\\"hello\\""', '"GREETING=\\"howdy\\""'))
        result = run([*build, "app"], project)
        assert summary(result.stderr, "app")[0] == 4, result.stderr
        assert run([project / "out/app/app"], project).stdout == b"howdy 53\n"

        # Readable C for building without Cesta: plain make reproduces the build.
        exported = project / "exported"
        result = run([*build, "--export-c", "exported"], project)
        files = sorted(str(f.relative_to(exported)) for f in exported.rglob("*") if f.is_file())
        assert not [f for f in files if f.endswith(".cesta")], files
        assert "src/main.c" in files and "src/one.S" in files and "include/values.h" in files, files
        assert not [f for f in files if f.endswith(".c") and b"#line" in (exported / f).read_bytes()], files
        run(["make", "-s", "-j4", "-C", exported, "CC=" + compiler], project)
        assert run([exported / "out/app/app"], exported).stdout == b"howdy 53\n"
        assert subprocess.run([exported / "out/bare/bare"], cwd=exported).returncode == 3

        # Errors name the authored unit by its project path.
        (project / "src/twice.cesta").write_text("(unit (fn int twice ((int x)) (return (+ x missing))))\n")
        result = run([*build, "app"], project, success=False)
        text = result.stderr.decode()
        assert re.search(r"^src/twice\.cesta:1:\d+: error: .*missing", text, re.M), text
        assert "cesta-generated" not in text, text
        # A malformed project is reported at its own position.
        (project / "cesta.project").write_text(PROJECT.replace("(kind archive)", "(kind library)"))
        result = run(build, project, success=False)
        assert re.search(r"^cesta\.project:8:\d+: error: .*kind", result.stderr.decode(), re.M), result.stderr
        (project / "cesta.project").write_text(PROJECT)
        result = run([*build, "nonexistent"], project, success=False)
        assert b"no target nonexistent" in result.stderr
        records.append({"compiler": compiler, "targets": 3, "incremental": True, "freestanding_static_pie": True,
                        "export_with_make": True})
        print(compiler + ": project build, archive, mixed sources, freestanding static PIE, incremental rebuilds, export built by make and routed errors passed", flush=True)
        dependency_controls(compiler, project / "dependencies")
        freestanding_macro(compiler, project / "macro-pie")
        response_controls(compiler, project / "response-inputs")
    (WORK / "summary.json").write_text(json.dumps({"records": records}, indent=2) + "\n")


if __name__ == "__main__":
    main()
