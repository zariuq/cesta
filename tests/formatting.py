"""Arena formatting: long paths, reusable arguments and explicit failures."""

from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent

SOURCE = r'''
#include "cesta.h"
#include <assert.h>
#include <locale.h>
#include <string.h>
#include <wchar.h>

static void repeat(CeArena *arena, const char *format, ...) {
    va_list arguments;
    va_start(arguments, format);
    char *first = ce_vformat(arena, format, arguments);
    char *second = ce_vformat(arena, format, arguments);
    va_end(arguments);
    assert(strcmp(first, "field:17:4294967295") == 0);
    assert(strcmp(first, second) == 0);
}

int main(int argc, char **argv) {
    CeArena arena = {0};
    assert(setlocale(LC_CTYPE, "C"));
    if (argc == 2 && strcmp(argv[1], "null") == 0) {
        ce_format(&arena, NULL);
        return 1;
    }
    if (argc == 2 && strcmp(argv[1], "encoding") == 0) {
        ce_format(&arena, "%lc", (wint_t)0x100);
        return 1;
    }
    char prefix[8193];
    memset(prefix, 'x', sizeof(prefix)-1);
    prefix[sizeof(prefix)-1] = 0;
    char *path = ce_format(&arena, "%s/%u.%s", prefix, 17u, "c");
    assert(memcmp(path, prefix, sizeof(prefix)-1) == 0);
    assert(strcmp(path+sizeof(prefix)-1, "/17.c") == 0);
    assert(strcmp(ce_format(&arena, "%s", ""), "") == 0);
    assert(strcmp(ce_format(&arena, "%s.XXXXXX", "a % b"), "a % b.XXXXXX") == 0);
    assert(memcmp(ce_format(&arena, "%cX", 0), "\0X\0", 3) == 0);
    repeat(&arena, "%s:%u:%u", "field", 17u, 4294967295u);
    ce_arena_free(&arena);
    return 0;
}
'''


def run(arguments):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True)
    assert result.returncode == 0, (arguments, result.returncode, result.stderr.decode(errors='replace'))


def main():
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='formatting-', dir=ROOT / 'build') as temporary:
        work = Path(temporary)
        source = work / 'formatting.c'
        source.write_text(SOURCE)
        routes = 0
        refusals = 0
        for compiler in ('gcc', 'clang'):
            for sanitize in (False, True):
                flags = ['-O1' if sanitize else '-O2', '-g0', '-std=c11',
                         '-Wall', '-Wextra', '-Wpedantic', '-Werror']
                if sanitize:
                    flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                              '-fno-omit-frame-pointer']
                binary = work / (compiler + ('-sanitize' if sanitize else ''))
                run([compiler, *flags, '-Isrc', 'src/base.c', str(source), '-o', str(binary)])
                run([str(binary)])
                routes += 1
                for mode, message in [('null', b'null format string'), ('encoding', b'cannot format text')]:
                    result = subprocess.run([str(binary), mode], capture_output=True)
                    assert result.returncode == 2 and b'cesta: ' + message in result.stderr, (
                        compiler, sanitize, mode, result.returncode, result.stderr)
                    refusals += 1
    print(f'formatting: {routes} GCC/Clang routes, long paths and reusable va_list; '
          f'{refusals} explicit failure controls', flush=True)


if __name__ == '__main__':
    main()
