"""User builds, checks and exported programs work without a proof assistant."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def run(arguments, cwd, environment):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, env=environment, capture_output=True)
    assert result.returncode == 0, (arguments, result.returncode, result.stderr.decode(errors='replace'))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cesta', type=Path, default=ROOT / 'build/cesta')
    parser.add_argument('--bootstrap-only',action='store_true',
                        help='build clean bundled Cesta sources with Python and provers trapped')
    args = parser.parse_args()
    binary = args.cesta.resolve()
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='user-standalone-', dir=ROOT / 'build') as temporary:
        work = Path(temporary)
        traps = work / 'tools'
        traps.mkdir()
        marker = work / 'unexpected-prover'
        for name in (('python','python3','lean','lake') if args.bootstrap_only else ('lean','lake')):
            program = traps / name
            program.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$CESTA_UNEXPECTED_PROVER"\nexit 99\n')
            program.chmod(0o755)
        environment = {**os.environ, 'PATH':str(traps)+os.pathsep+os.environ.get('PATH',''),
                       'CESTA_UNEXPECTED_PROVER':str(marker), 'LEAN_PATH':str(work/'absent-lean-library')}
        if args.bootstrap_only:
            for compiler in ('gcc','clang'):
                source=work/compiler;source.mkdir()
                for directory in ('src','grammar','presentations'):
                    shutil.copytree(ROOT/directory,source/directory)
                (source/'tools').mkdir()
                shutil.copy2(ROOT/'tools/embed.c',source/'tools/embed.c')
                shutil.copy2(ROOT/'Makefile',source/'Makefile')
                run(['make','-s','CC='+compiler,'all'],source,environment)
            assert not marker.exists(),marker.read_text() if marker.exists() else ''
            print('bundled Cesta: clean GCC/Clang source builds; Python, Lean and Lake traps never invoked',flush=True)
            return
        for compiler in ('gcc', 'clang'):
            project = work / compiler
            project.mkdir()
            (project/'words.h').write_text('#include <stdint.h>\n#include <stddef.h>\n#include <stdbool.h>\n'
                'typedef struct Words {const uint32_t *data;} Words;\n'
                'typedef struct TraceContext {unsigned calls;} TraceContext;\n'
                'bool touch(TraceContext *,const uint32_t *);\n')
            (project/'words.records').write_text('''(cesta record-visitors/1
              (owner words)
              (visitor read_word (owner words) (type "const uint32_t *")
                (call touch) (argument value) (effects read)
                (obligation "Reads its borrowed word and retains no pointer."))
              (record "Words" (function inspect_words) (context "TraceContext") (receiver read)
                (field data (borrow read) (owner words) (visit read_word))))''')
            run([binary,'record-visitor','--schema','words.records','-o','visitor.h',
                 '--cc',compiler,'-std=c11','words.h'],project,environment)
            body = '''uint32_t first(Words words){return *words.data;}
bool touch(TraceContext *context,const uint32_t *data){++context->calls;return *data==7;}
int main(void){uint32_t data=7;Words words={&data};TraceContext ctx={0};
return first(words)!=7 || !inspect_words(&ctx,&words) || ctx.calls!=1;}
'''
            (project/'app.cesta').write_text('(cesta native-c/1 compact-c/2 (unit '
                '(ownership (check-all) (record Words (field data (borrow read)))) (include "words.h") '
                '(include "visitor.h") '
                '(native '+json.dumps(body)+')))\n')
            (project/'cesta.project').write_text('(cesta project/1 (compiler '+compiler+') (out "out") '
                '(profile hosted (flags "-std=c11" "-O2" "-g0")) '
                '(target app (profile hosted) (sources "app.cesta") (include ".")))\n')
            exported = project/'exported'
            run([binary,'build','app'],project,environment)
            run([binary,'build','--export-c',exported,'app'],project,environment)
            direct = project/'out/app/app'
            run([direct],project,environment)
            plan = project/'app.plan'
            source = project/'app.c'
            run([binary,'export-c','app.cesta','--cc',compiler,'--plan',plan,'-o',source,'--','-I.'],project,environment)
            assert plan.is_file() and b'Ownership' in plan.read_bytes()
            run([binary,'bind','--fields','--json-lines','--cc',compiler,'-std=c11','-I.',source],project,environment)
            makefile = (exported/'GNUmakefile').read_text()
            assert str(binary) not in makefile
            run(['make','-s','CC='+compiler],exported,environment)
            plain = exported/'out/app/app'
            run([plain],project,environment)
            assert direct.read_bytes() == plain.read_bytes(), compiler
        assert not marker.exists(), marker.read_text() if marker.exists() else ''
    print('standalone users: GCC/Clang record visitors, checked project builds, field queries, plans, '
          'plain-C exports and identical executables; Lean/Lake trap never invoked', flush=True)


if __name__ == '__main__':
    main()
