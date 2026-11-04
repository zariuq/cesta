"""Generators and per-source options share the build/export dependency graph."""
from pathlib import Path
import subprocess
import tempfile
import shutil

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'build/cesta'
WORK = Path(tempfile.mkdtemp(prefix='generators-', dir=ROOT / 'build'))


def run(args, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, text=True)
    assert (result.returncode == 0) == success, (args, result.returncode, result.stdout, result.stderr)
    return result


def build(success=True):
    return run([BINARY, 'build', '-j', '2', 'app'], success=success)


def main():
    (WORK / 'value.txt').write_text('7\n')
    (WORK / 'extra.txt').write_text('first\n')
    original_makefile = 'SENTINEL=the source Makefile is input data\nall:\n\t@false\n'
    (WORK / 'Makefile').write_text(original_makefile)
    script = '''from pathlib import Path
import sys
count=Path('runs'); count.write_text(str(int(count.read_text())+1) if count.exists() else '1')
value=int(Path('value.txt').read_text())
Path('gen/value.h').write_text('#define VALUE '+str(value)+'\\n')
if '--fail' in sys.argv: raise SystemExit(1)
Path('gen/value.c').write_text('#include "value.h"\\n#ifndef SPECIAL\\n#error source flags missing\\n#endif\\nint value(void){return VALUE+SPECIAL;}\\n')
'''
    (WORK / 'generate.py').write_text(script)
    (WORK / 'main.c').write_text('#include <stdio.h>\n#ifdef SPECIAL\n#error source flags leaked\n#endif\nint value(void); int main(void){printf("%d\\n",value());}\n')
    project = '''(cesta project/1 (compiler gcc) (out "out")
 (target data (kind generator) (inputs "generate.py" "value.txt" "Makefile")
   (outputs "gen/value.h" "gen/value.c") (command "python3" "generate.py"))
 (target app (flags "-std=c11" "-O2" "-Wall" "-Werror") (depends data)
   (compile-inputs "extra.txt")
   (sources "main.c") (source "gen/value.c" (define "SPECIAL=3") (include "gen")))
 (target unselected (kind generator) (outputs "unused") (command "false")))
'''
    (WORK / 'cesta.project').write_text(project)
    build()
    assert run([WORK / 'out/app/app']).stdout == '10\n'
    assert (WORK / 'runs').read_text() == '1'
    (WORK / 'extra.txt').write_text('second\n')
    assert 'app: 2 compiled' in build().stderr
    assert (WORK / 'runs').read_text() == '1'
    build()
    assert (WORK / 'runs').read_text() == '1'
    (WORK / 'value.txt').write_text('9\n')
    build()
    assert (WORK / 'runs').read_text() == '2'
    assert run([WORK / 'out/app/app']).stdout == '12\n'
    (WORK / 'gen/value.c').unlink()
    build()
    assert (WORK / 'runs').read_text() == '3'
    failed = project.replace('"generate.py"))', '"generate.py" "--fail"))')
    (WORK / 'cesta.project').write_text(failed)
    build(False); build(False)
    assert (WORK / 'runs').read_text() == '5', 'partial outputs must not become current'
    (WORK / 'cesta.project').write_text(project)
    build()
    reference = (WORK / 'out/app/app').read_bytes()
    run([BINARY, 'build', '--export-c', 'export', 'app'])
    assert (WORK / 'export/Makefile').read_text() == original_makefile
    assert (WORK / 'export/GNUmakefile').read_text().startswith('# Readable C exported by cesta build')
    run(['make', '-j2'], cwd=WORK / 'export')
    assert (WORK / 'export/out/app/app').read_bytes() == reference
    (WORK / 'export/value.txt').write_text('11\n')
    run(['make', '-j2'], cwd=WORK / 'export')
    assert run([WORK / 'export/out/app/app']).stdout == '14\n'
    (WORK / 'export/generate.py').write_text(script + '\nraise SystemExit(1)\n')
    run(['make', '-j2'], cwd=WORK / 'export', success=False)
    attempts = int((WORK / 'export/runs').read_text())
    run(['make', '-j2'], cwd=WORK / 'export', success=False)
    assert int((WORK / 'export/runs').read_text()) == attempts + 1
    for bad in (
        project.replace('(depends data)', '(depends data app)'),
        project.replace('(compile-inputs "extra.txt")', '(compile-inputs "missing.txt")'),
        project.replace('(outputs "unused")', '(outputs "gen/value.c")'),
        project.replace('(outputs "gen/value.h" "gen/value.c")', '(outputs "gen/value.h" "gen/missing.c")'),
        project.replace('(source "gen/value.c"', '(source "gen/value.c" (flags)) (source "gen/value.c"'),
    ):
        (WORK / 'cesta.project').write_text(bad)
        build(False)
    (WORK / 'GNUmakefile').write_text('reserved source input\n')
    (WORK / 'cesta.project').write_text(project.replace('"extra.txt"', '"GNUmakefile"'))
    refused = run([BINARY, 'build', '--export-c', 'collision', 'app'], success=False)
    assert 'export reserves GNUmakefile' in refused.stderr
    cache = WORK / 'cache'
    cache.mkdir()
    (cache / 'plain.c').write_text('int main(void){return 0;}\n')
    (cache / 'authored.cesta').write_text('(unit (fn int main (void) (return 0)))\n')
    (cache / 'cesta.project').write_text('''(cesta project/1 (compiler gcc) (out "out")
      (target plain (sources "plain.c")) (target authored (sources "authored.cesta")))''')
    for name in ('first-cesta', 'second-cesta'):
        shutil.copy2(BINARY, cache / name)
    run([cache / 'first-cesta', 'build'], cwd=cache)
    changed_tool = run([cache / 'second-cesta', 'build'], cwd=cache)
    assert 'plain: 0 compiled, 1 up to date' in changed_tool.stderr
    assert 'authored: 1 compiled, 0 up to date' in changed_tool.stderr
    census = run([cache / 'second-cesta', 'build', '--read-warnings', 'plain'], cwd=cache)
    assert 'plain: 1 compiled' in census.stderr and 'syntax and types admitted' in census.stderr
    print('project generators: ordering, selection, freshness, partial failures, source flags and regenerating C export passed')


if __name__ == '__main__':
    main()
