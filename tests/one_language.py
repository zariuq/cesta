"""Cesta compiles as one language: what the native compiler and debugger
report about a .cesta unit names that unit's lines and columns."""

import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/one-language-tests"


def run(args, cwd, success=True, env=None):
    result = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, env=env)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{args}\nstatus {result.returncode}\n{result.stderr.decode(errors='replace')}")
    return result


def document(*forms):
    return "(cesta native-c/1 compact-c/2\n  (unit\n" + "".join("    " + f + "\n" for f in forms) + "  ))\n"


def position(text, line, needle):
    """1-based column of `needle` on 1-based `line` of `text`."""
    column = text.splitlines()[line - 1].index(needle) + 1
    return line, column


def diagnostics(stderr):
    """(path, line, column, kind, message) for each located diagnostic, and the
    excerpt that follows it as (line number, text, caret column)."""
    found = []
    lines = stderr.decode(errors="replace").splitlines()
    for i, text in enumerate(lines):
        m = re.match(r"(.+?):(\d+):(\d+): (error|warning|note|fatal error): (.*)", text)
        if not m:
            continue
        excerpt = None
        if i + 2 < len(lines):
            source = re.match(r"\s*(\d+) \| (.*)", lines[i + 1])
            caret = re.match(r"\s*\| (\s*)\^", lines[i + 2])
            if source and caret:
                excerpt = (int(source.group(1)), source.group(2), len(caret.group(1)) + 1)
        found.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5), excerpt))
    return found


def check_located(found, path, text, line, columns, kind, words):
    match = [d for d in found if d[0] == path and d[3] == kind and all(w in d[4] for w in words)]
    assert match, (path, kind, words, found)
    d = match[0]
    assert d[1] == line and d[2] in columns, (d, line, columns)
    assert d[5] == (line, text.splitlines()[line - 1], d[2]), d


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    records = []
    for compiler in ("gcc", "clang"):
        project = WORK / compiler
        project.mkdir()
        cc = [BINARY, "cc", "--cc", compiler]

        # Compiler errors and notes land on the authored atom, with its line shown.
        bad = document(
            "(include <stdio.h>)",
            "(fn int add ((int a) (int b)) (return (+ a b)))",
            "(fn int main (void)",
            "  (var int x (call add 1))",
            "  (call printf \"%d\\n\" x)",
            "  (return 0))")
        (project / "bad.cesta").write_text(bad)
        result = run([*cc, "-c", "bad.cesta", "-o", "bad.o"], project, success=False)
        found = diagnostics(result.stderr)
        call_line = 6
        check_located(found, "bad.cesta", bad, call_line,
                      {position(bad, call_line, "add 1")[1], position(bad, call_line, "(call add")[1]},
                      "error", ["argument"])
        check_located(found, "bad.cesta", bad, 4, {position(bad, 4, "add")[1]}, "note", ["declared"])
        assert b"cesta-generated" not in result.stderr and b".c:" not in result.stderr, result.stderr

        # Warnings are routed the same way.
        warn = document("(fn int main (void)", "  (var int unused 1)", "  (return 0))")
        (project / "warn.cesta").write_text(warn)
        result = run([*cc, "-Wall", "-c", "warn.cesta", "-o", "warn.o"], project)
        found = diagnostics(result.stderr)
        check_located(found, "warn.cesta", warn, 4, set(range(1, 40)), "warning", ["unused"])

        # Cesta's own errors use the same form.
        wrong = document("(fn int main (void)", "  (frobnicate 1 2)", "  (return 0))")
        (project / "wrong.cesta").write_text(wrong.replace("(fn int main (void)", "(fn int main"))
        result = run([*cc, "-c", "wrong.cesta", "-o", "wrong.o"], project, success=False)
        found = diagnostics(result.stderr)
        assert found and found[0][0] == "wrong.cesta" and found[0][3] == "error", result.stderr
        assert found[0][5] is not None, result.stderr

        # Errors in an included header name the including authored line.
        (project / "broken.h").write_text("int broken(void) { return }\n")
        header = document("(include \"broken.h\")", "(fn int main (void) (return 0))")
        (project / "header.cesta").write_text(header)
        result = run([*cc, "-c", "header.cesta", "-o", "header.o"], project, success=False)
        text = result.stderr.decode(errors="replace")
        assert "header.cesta:3" in text and "broken.h:1" in text and "cesta-generated" not in text, text

        # Linking several units reports every unit's errors.
        (project / "second.cesta").write_text(document("(fn int second (void) (return missing))"))
        result = run([*cc, "bad.cesta", "second.cesta", "-o", "both"], project, success=False)
        files = {d[0] for d in diagnostics(result.stderr) if d[3] == "error"}
        assert files == {"bad.cesta", "second.cesta"}, result.stderr

        # The program's own view of its source: __LINE__, __FILE__ and assert.
        good = document(
            "(include <stdio.h>)", "(include <assert.h>)",
            "(fn int add ((int a) (int b)) (return (+ a b)))",
            "(fn int main ((int argc) (char (** argv)))",
            "  (call printf \"%s %d\\n\" __FILE__ __LINE__)",
            "  (call assert (< argc 2))",
            "  (return (- (call add 1 2) 3)))")
        (project / "good.cesta").write_text(good)
        run([*cc, "-g", "-O0", "good.cesta", "-o", "good"], project)
        output = run([project / "good"], project).stdout.decode()
        assert output == f"good.cesta 7\n", output
        aborted = run([project / "good", "x"], project, success=False).stderr.decode()
        assert "good.cesta:8" in aborted, aborted

        # Debug line tables name the authored file, within its lines.
        table = run(["objdump", "--dwarf=decodedline", project / "good"], project).stdout.decode()
        rows = [(name, int(line)) for name, line in re.findall(r"^(\S+)\s+(\d+)\s+0x", table, re.M)]
        authored = [line for name, line in rows if name == "good.cesta"]
        assert authored and not [name for name, _ in rows if name.endswith(".c")], table
        assert all(1 <= line <= len(good.splitlines()) for line in authored), authored
        # A debugger stops on an authored line and shows it.
        session = run(["gdb", "-q", "-batch", "-ex", "break good.cesta:9", "-ex", "run",
                       "-ex", "info line", project / "good"], project).stdout.decode()
        assert "good.cesta:9" in session or "good.cesta, line 9" in session or "Line 9 of \"good.cesta\"" in session, session

        # Default object name follows the native convention.
        run([*cc, "-c", "good.cesta"], project)
        assert (project / "good.o").exists()

        # Readable export carries no line directives; preprocessing names the unit.
        exported = run([BINARY, "export-c", "-o", project / "good.export.c", "good.cesta", "--"], project)
        assert b"#line" not in (project / "good.export.c").read_bytes()
        preprocessed = run([*cc, "-E", "good.cesta"], project).stdout
        assert b'"good.cesta"' in preprocessed

        # Statements inside a conditional branch carry their own lines.
        branch = document(
            "(fn int main (void)",
            "  (cpp-if \"1\"",
            "    (then",
            "      (var int y 0)",
            "      (return (call undefined_function y)))",
            "    (else (return 0))))")
        (project / "branch.cesta").write_text(branch)
        result = run([*cc, "-std=c11", "-c", "branch.cesta", "-o", "branch.o"], project, success=False)
        lines = {d[1] for d in diagnostics(result.stderr) if d[0] == "branch.cesta"}
        assert 7 in lines, result.stderr

        # Units written as a bare (unit ...) compile through the AST path, routed the same way.
        ast = "(unit\n  (fn int add ((int a) (int b)) (return (+ a b)))\n  (fn int main (void)\n" \
              "    (var int x (call add 1))\n    (return x)))\n"
        (project / "ast.cesta").write_text(ast)
        result = run([*cc, "-std=gnu11", "-c", "ast.cesta", "-o", "ast.o"], project, success=False)
        found = diagnostics(result.stderr)
        check_located(found, "ast.cesta", ast, 4,
                      {position(ast, 4, "add 1")[1], position(ast, 4, "(call add")[1]}, "error", ["argument"])
        check_located(found, "ast.cesta", ast, 2, {position(ast, 2, "add")[1]}, "note", ["declared"])

        # Intermediates stay with the outputs, never in a build/ of the caller.
        nested = project / "out"
        nested.mkdir()
        run([*cc, "-c", "good.cesta", "-o", "out/good.o"], project)
        assert (nested / ".cesta-generated").is_dir() and not (project / "build").exists()

        records.append({"compiler": compiler, "routed": ["error", "note", "warning", "cesta", "header", "link", "ast"],
                        "debug_rows": len(authored), "gdb": True})
        print(compiler + ": routed diagnostics, __LINE__/__FILE__/assert, debug lines, gdb and export passed", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records}, indent=2) + "\n")


if __name__ == "__main__":
    main()
