"""Native compiler boundaries for compact source forms and mixed projects."""

import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/native-authoring-tests"


def run(args, cwd=WORK, success=True, env=None):
    result = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, env=env)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{args}\nstatus {result.returncode}\n{result.stderr.decode(errors='replace')}")
    return result


def document(*forms):
    return "(cesta native-c/1 compact-c/2 (unit " + " ".join(forms) + "))\n"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    records = []
    # This fixture deliberately exercises what the native compiler knows beyond
    # the structural C AST: includes, token pasting, stringification, variadics,
    # conditionals and unparsed inactive source.
    body = document(
        '(include <stdio.h>)', '(include <stddef.h>)',
        '(define HEADER "\\\"local.h\\\"")', '(include HEADER)',
        '(define (CAT a b) "a ## b")', '(define (STR x) "#x")',
        '(define (PRINT ...) "printf(__VA_ARGS__)")',
        '(define SCRATCH 77)', '(undef SCRATCH)',
        '(comment "Keep this comment and the source order.")',
        '(cpp-if "0" (then (native "not valid C ! @")) '
        '(elif "defined(SCRATCH)" (native "also not valid C")))',
        '(cpp-if "VALUE > 5" (then (var int high 1)) '
        '(elif "VALUE > 2" (var int high 2)) (else (var int high 3)))',
        '(fn int answer () (return (+ (native "1 + 2") VALUE)))',
        '(fn int main () (var size_t n 3) '
        '(cpp-if "defined(QUIET)" (then (return (!= n 3))) '
        '(else (call PRINT "%s %d %d %lu\\n" (call STR hi) '
        '(call CAT ans wer) high (cast (unsigned long) n)) (return 0))))')
    # CAT produces the call target, so retain the explicit invocation as C.
    body = body.replace('(call CAT ans wer)', '(native "CAT(ans,wer)()")')
    negatives = {
        'extra-document': body + body,
        'bad-version': body.replace('native-c/1', 'native-c/99', 1),
        'wrong-sort': document('(fn int (native "main") () (return 0))'),
        'bad-include': document('(include "x\\\"\\n#error injected")'),
        'bad-comment': document('(comment "closed */ int x;")'),
        'else-order': document('(cpp-if "1" (then) (else) (elif "0"))'),
        'missing-then': document('(cpp-if "1" (else))'),
        'bad-define': document('(define N "1\\n#error injected")'),
        'bad-variadic': document('(define (F ... x) "x")'),
        'empty-define-name': document('(define () "")'),
        'missing-native-string': document('(native 123)'),
        'unknown-compact-form': document('(invent-a-function x)')}
    for name, text in negatives.items():
        path = WORK / (name + '.cesta'); path.write_text(text)
        result = run([BINARY, 'expand', path], success=False)
        assert str(path).encode() in result.stderr and b':' in result.stderr
    for compiler in ('gcc', 'clang'):
        project = WORK / compiler; project.mkdir(exist_ok=True)
        source = project / 'program.cesta'; source.write_text(body)
        (project / 'local.h').write_text('#define VALUE 7\n')
        expanded = run([BINARY, 'expand', source], cwd=project).stdout
        assert b'#include HEADER' in expanded and b'#define CAT(a,b) a ## b' in expanded
        assert b'/*Keep this comment and the source order.*/' in expanded
        assert expanded.index(b'#undef SCRATCH') < expanded.index(b'#if 0')
        native = project / 'program.c'; native.write_bytes(expanded)
        flags = ['-std=c11', '-O2', '-g0', '-Wall', '-Wextra', '-Werror']
        reference = project / 'reference.o'; actual = project / 'actual.o'
        run([compiler, *flags, '-c', native, '-o', reference], cwd=project)
        run([BINARY, 'cc', '--cc', compiler, *flags, '-MMD', '-MP', '-c', source, '-o', actual], cwd=project)
        assert actual.read_bytes() == reference.read_bytes(), compiler + ': changed object bytes'
        # Independently written C checks the compact authoring contract, rather
        # than just comparing two ways to compile the emitter's own output.
        handwritten = project / 'handwritten'; handwritten.mkdir(exist_ok=True)
        (handwritten / 'local.h').write_text('#define VALUE 7\n')
        (handwritten / 'program.c').write_text(
            '#include <stdio.h>\n#include <stddef.h>\n'
            '#define HEADER "local.h"\n#include HEADER\n'
            '#define CAT(a,b) a ## b\n#define STR(x) #x\n'
            '#define PRINT(...) printf(__VA_ARGS__)\n'
            '#define SCRATCH 77\n#undef SCRATCH\n'
            '/* Keep this comment and the source order. */\n'
            '#if 0\nnot valid C ! @\n#elif defined(SCRATCH)\nalso not valid C\n#endif\n'
            '#if VALUE > 5\nint high = 1;\n#elif VALUE > 2\nint high = 2;\n#else\nint high = 3;\n#endif\n'
            'int answer(void) { return (1 + 2) + VALUE; }\n'
            'int main(void) { size_t n = 3;\n#ifdef QUIET\nreturn n != 3;\n#else\n'
            'PRINT("%s %d %d %lu\\n", STR(hi), CAT(ans,wer)(), high, (unsigned long)n);\n'
            'return 0;\n#endif\n}\n')
        run([compiler, *flags, '-c', 'program.c', '-o', 'handwritten.o'], cwd=handwritten)
        assert actual.read_bytes() == (handwritten / 'handwritten.o').read_bytes(), compiler + ': handwritten C differs'
        dependency = actual.with_suffix('.d').read_text()
        assert 'program.cesta' in dependency and 'local.h' in dependency
        assert 'cesta-generated' not in dependency and 'stdio.h' not in dependency
        executable = project / 'program'
        run([compiler, actual, '-o', executable], cwd=project)
        assert run([executable], cwd=project).stdout == b'hi 10 1 3\n'
        # No up-front C11 restriction on native authoring: the selected compiler
        # is responsible for whether emitted constructs belong to its dialect.
        run([BINARY, 'cc', '--cc', compiler, '-std=c23', '-c', source, '-o', project / 'c23.o'], cwd=project)
        # Native compiler diagnostics, including a pragma deliberately unsupported
        # by Cesta's optional preprocessor, must reach the caller.
        poison = project / 'poison.cesta'
        poison.write_text(document('(native "#pragma GCC poison forbidden\\nint forbidden;\\n")'))
        denied = run([BINARY, 'cc', '--cc', compiler, '-c', poison, '-o', project / 'poison.o'], cwd=project, success=False)
        assert b'poison' in denied.stderr
        # Ordinary C uses the native route including response files and a newer
        # C standard; its bytes are not read or rewritten by the Cesta frontend.
        passthrough = project / 'plain.c'
        passthrough.write_text('#include <stddef.h>\n#define N 3\n#if N > 2\n'
                               '/* keep */ int plain(void){typeof_unqual(1) x=N;return x;}\n#endif\n')
        before = passthrough.read_bytes()
        response = project / 'flags.rsp'
        response.write_text('-std=c23 -O2 -g0 -c plain.c -o plain.o\n')
        run([BINARY, 'cc', '--cc', compiler, '@flags.rsp'], cwd=project)
        run([compiler, '-std=c23', '-O2', '-g0', '-c', passthrough, '-o', project / 'plain-reference.o'], cwd=project)
        assert (project / 'plain.o').read_bytes() == (project / 'plain-reference.o').read_bytes()
        assert passthrough.read_bytes() == before
        # Compact inputs may live inside nested response files; quoted words and
        # escaped quotation marks must reach the native compiler intact.
        response.write_text('-std=c11 -O2 -g0 -c @compact-inner.rsp\n')
        (project / 'compact-inner.rsp').write_text('"program.cesta" -o "response compact.o"\n')
        run([BINARY, 'cc', '--cc', compiler, '@flags.rsp'], cwd=project)
        assert (project / 'response compact.o').read_bytes() == actual.read_bytes()
        quoted = project / 'quoted argument.cesta'
        quoted.write_text(document('(include <string.h>)',
            '(fn int main () (return (call strcmp MESSAGE "hello world")))'))
        response.write_text('-std=c11 -O2 -g0 -DMESSAGE=\\"hello\\ world\\" '
                            '"quoted argument.cesta" -o "quoted program"\n')
        run([BINARY, 'cc', '--cc', compiler, '@flags.rsp'], cwd=project)
        run([project / 'quoted program'], cwd=project)
        (project / 'cyclic.rsp').write_text('program.cesta @cyclic.rsp\n')
        cycle = run([BINARY, 'cc', '--cc', compiler, '@cyclic.rsp'], cwd=project, success=False)
        assert b'recursive compiler response file' in cycle.stderr
        # Independent native include directories in a mixed compilation.
        for name, value in [('one', 11), ('two', 23)]:
            directory = project / name; directory.mkdir(exist_ok=True)
            (directory / 'local.h').write_text(f'#define VALUE {value}\n')
            (directory / 'part.cesta').write_text(document('(include "local.h")',
                f'(fn int {name} () (return VALUE))'))
        mixed = project / 'mixed.c'
        mixed.write_text('int one(void),two(void);int main(void){return one()!=11||two()!=23;}\n')
        run([BINARY, 'cc', '--cc', compiler, *flags, 'mixed.c', 'one/part.cesta', 'two/part.cesta', '-o', 'mixed'], cwd=project)
        run([project / 'mixed'], cwd=project)
        for option in ('-M', '-MM'):
            deps = run([BINARY, 'cc', '--cc', compiler, option, '-MQ', 'target space.o', source], cwd=project).stdout
            assert b'target\\ space.o:' in deps and b'program.cesta' in deps and b'local.h' in deps
            assert b'cesta-generated' not in deps
        run([BINARY, 'cc', '--cc', compiler, '-MM', '-MF', 'explicit.d', source], cwd=project)
        assert 'program.cesta' in (project / 'explicit.d').read_text()
        stdout_deps = run([BINARY, 'cc', '--cc', compiler, *flags, '-MMD', '-MF', '-',
                           '-c', source, '-o', 'stdout-deps.o'], cwd=project).stdout
        assert b'program.cesta' in stdout_deps and b'local.h' in stdout_deps
        assert (project / 'stdout-deps.o').read_bytes() == actual.read_bytes()
        preprocessed = run([BINARY, 'cc', '--cc', compiler, '-E', '-P', source], cwd=project).stdout
        expected = run([compiler, '-E', '-P', native], cwd=project).stdout
        assert preprocessed == expected
        # Different preprocessing flags build independently even while sharing
        # immutable generated C input.
        def simultaneous(number):
            out = project / ('parallel-' + str(number))
            command = [BINARY, 'cc', '--cc', compiler, *flags]
            if number % 2: command.append('-DQUIET')
            run([*command, source, '-o', out], cwd=project)
            return run([out], cwd=project).stdout
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(simultaneous, range(4))) == [b'hi 10 1 3\n', b'', b'hi 10 1 3\n', b'']
        # Ordinary make owns the dependency graph. Header changes rebuild only
        # the dependent compact translation unit.
        makefile = project / 'Makefile'
        makefile.write_text('.DEFAULT_GOAL := made\nCFLAGS := -std=c11 -O2 -g0 -Wall -Wextra -Werror\n'
                            'CPPFLAGS := -MMD -MP\n'
                            f'include {ROOT}/cesta.mk\n'
                            'made: program.o\n\t$(CC) program.o -o made\n-include program.d\n')
        args = ['make', '-s', '-j2', 'CC=' + compiler, 'CESTA=' + str(BINARY)]
        run(args, cwd=project)
        obj = project / 'program.o'; stamp = obj.stat().st_mtime_ns
        run(args, cwd=project); assert obj.stat().st_mtime_ns == stamp
        (project / 'local.h').write_text('#define VALUE 8\n')
        run(args, cwd=project); assert obj.stat().st_mtime_ns != stamp
        assert run([project / 'made'], cwd=project).stdout == b'hi 11 1 3\n'
        records.append({'compiler': compiler, 'object_sha256': hashlib.sha256(actual.read_bytes()).hexdigest(),
                        'mixed_include_contexts': True, 'native_preprocessing': True,
                        'parallel_configurations': 4, 'incremental_make': True,
                        'plain_C_response_file': True, 'native_C23': True})
        print(compiler + ': native forms, object identity, mixed sources, native diagnostics and make passed', flush=True)
    (WORK / 'summary.json').write_text(json.dumps({'records': records, 'refusals': len(negatives),
        'binary_sha256': hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + '\n')


if __name__ == '__main__':
    main()
