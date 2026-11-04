"""Reader warnings preserve compiler output, status and response-file handling."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'build/cesta'
WORK = Path(tempfile.mkdtemp(prefix='reader-warnings-', dir=ROOT / 'build'))


def run(args):
    return subprocess.run(list(map(str, args)), cwd=WORK, capture_output=True, text=True)


def main():
    count = 0
    for cc in ('gcc', 'clang'):
        if not shutil.which(cc):
            continue
        for source, text, admitted in (
            ('good.c', 'struct S { int native, include; }; int f(void){return sizeof(struct S);}\n', True),
            # Native compilers accept this extension even when the Cesta reader does not.
            ('extension.c', 'typedef int v4 __attribute__((vector_size(16))); v4 f(v4 x){return __builtin_shufflevector(x,x,0,1,2,3);}\n', False),
        ):
            (WORK / source).write_text(text)
            flags = ['-std=gnu11', '-O2', '-Werror', '-c', source]
            native = run([cc, *flags, '-o', 'native.o'])
            assert native.returncode == 0, native.stderr
            checked = run([BINARY, 'cc', '--read-warnings', '--cc', cc, *flags, '-o', 'checked.o'])
            assert checked.returncode == native.returncode, checked.stderr
            assert (WORK / 'native.o').read_bytes() == (WORK / 'checked.o').read_bytes()
            assert 'cesta cc: read:' in checked.stderr or 'cesta cc: warning:' in checked.stderr
            if admitted:
                assert 'syntax and types admitted' in checked.stderr, checked.stderr
            else:
                assert 'warning:' in checked.stderr, checked.stderr
            (WORK / 'args.rsp').write_text(' '.join(['--read-warnings', '--cc', cc, *flags, '-o', 'response.o']))
            response = run([BINARY, 'cc', '@args.rsp'])
            assert response.returncode == 0, response.stderr
            assert (WORK / 'native.o').read_bytes() == (WORK / 'response.o').read_bytes()
            count += 1
        (WORK / 'bad.c').write_text('int f(void){return unknown;}\n')
        native_error = run([cc, '-Werror', '-c', 'bad.c', '-o', 'bad.o'])
        checked_error = run([BINARY, 'cc', '--read-warnings', '--cc', cc, '-Werror', '-c', 'bad.c', '-o', 'bad.o'])
        assert checked_error.returncode == native_error.returncode != 0
        # A target with no supported ABI must stay a warning, including under -Werror.
        flags = [*(['--target=riscv64-none-elf'] if cc == 'clang' else ['-mx32']), '-Werror', '-c', 'good.c']
        if run([cc, *flags, '-o', 'unknown-native.o']).returncode == 0:
            unknown = run([BINARY, 'cc', '--read-warnings', '--cc', cc, *flags, '-o', 'unknown.o'])
            assert unknown.returncode == 0 and 'warning:' in unknown.stderr and 'no ABI model' in unknown.stderr, unknown.stderr
            assert (WORK / 'unknown.o').read_bytes() == (WORK / 'unknown-native.o').read_bytes()
        pp = run([BINARY, 'cc', '--read-warnings', '--cc', cc, '-E', 'good.c'])
        assert pp.returncode == 0 and 'cesta cc:' not in pp.stderr
    assert count
    print(f'reader warnings: {count} byte-identical native/checked/response controls; native failures retained')


if __name__ == '__main__':
    main()
