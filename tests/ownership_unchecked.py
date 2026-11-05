"""Named unchecked definitions, survey mode and joined checked translation units.

An (unchecked NAME CONTRACT... (obligation "...")) form keeps one definition
out of the native ownership check. Its callers are still checked against the
declared contract, and the plan records it as a foreign obligation. A survey
(CESTA_OWNERSHIP_SURVEY=FILE) records every refused body and never admits the
unit. Two checked units joined into one translation unit compile.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent

# A checked caller and an unchecked byte-range reader the native profile refuses.
SUM = r'''
#include <stddef.h>
static int total(const int *values, size_t count) {
    int sum = 0;
    for (size_t i = 0; i < count; i++)
        sum += values[i];
    return sum;
}

typedef struct Row {
    int cells[4];
} Row;

int row_sum(const Row *row) {
    return total(&row->cells[0], 4);
}
'''
OBLIGATION = '(unchecked total (obligation "Reads count ints at values; callers pass an array of that length."))'

SECOND = r'''
int doubled(int value) {
    return value * 2;
}
'''

HARNESS = r'''
#include <stdio.h>
typedef struct Row { int cells[4]; } Row;
int row_sum(const Row *row);
int doubled(int value);
int main(void) {
    Row row = {{1, 2, 3, 4}};
    printf("%d %d\n", row_sum(&row), doubled(21));
    return 0;
}
'''

NEGATIVE = {
    'missing-obligation': (SUM, '(unchecked total)', 'foreign function requires a named obligation'),
    'declared-only': ('int outside(int);\nint caller(int x) {\n    return outside(x);\n}\n',
                      '(unchecked outside (obligation "x"))', 'call requires a checked body or an explicit foreign obligation'),
    'foreign-definition': (SUM, '(foreign total (obligation "x"))',
                           'a defined function must be checked, or named unchecked with its obligation'),
    'pointer-origin': ('static int *pick(int *a) {\n    return a + 1;\n}\nint use(int *a) {\n    return *pick(a);\n}\n',
                       '(unchecked pick (obligation "x"))', 'pointer result needs a declared borrow, static or ownership origin'),
    'foreign-pointer-origin': ('int *outside(int *a);\nint use(int *a) {\n    return *outside(a);\n}\n',
                               '(foreign outside (obligation "x"))', 'pointer return requires a lifetime origin'),
    'foreign-undeclared': ('int f(int x) {\n    return x;\n}\n', '(foreign missing (obligation "x"))',
                           'function contract has no C declaration'),
    'misspelt-unchecked-stays-checked': (SUM, '(unchecked totl (obligation "x"))',
                                         'dynamic pointer subscript requires a checked bounded view'),
    'caller-still-checked': ('static void both(int *a, int *b) {\n    *a = *b;\n}\nvoid f(int *a) {\n    both(a, a);\n}\n',
                             '(unchecked both (obligation "Writes *a from *b."))',
                             'overlap'),
}


def document(source, contracts):
    return ('(cesta native-c/1 compact-c/2 (unit (ownership (check-all) ' + contracts + ') (native '
            + json.dumps(source) + ')))\n')


def run(command, env=None, expect=0):
    result = subprocess.run(list(map(str, command)), cwd=ROOT, capture_output=True, text=True, env=env)
    if expect == 0:
        assert result.returncode == 0, (command, result.stderr[-3000:])
    else:
        assert result.returncode != 0, (command, 'was accepted')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cesta', type=Path, default=ROOT / 'build/cesta')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/ownership-unchecked')
    args = parser.parse_args()
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)

    first, second = work / 'sum.cesta', work / 'doubled.cesta'
    first.write_text(document(SUM, OBLIGATION))
    second.write_text(document(SECOND, ''))
    harness = work / 'main.c'
    harness.write_text(HARNESS)
    runs = 0
    for compiler in ('gcc', 'clang'):
        plan = work / f'sum-{compiler}.plan'
        exported = work / f'sum-{compiler}.c'
        run([args.cesta, 'export-c', first, '--std=c11', '--cc', compiler, '-o', exported, '--plan', plan])
        text = plan.read_text()
        assert '(foreign total' in text and 'Reads count ints at values' in text, text[:400]
        assert '(function row_sum' in text and '(function total' not in text, text[:400]
        other = work / f'doubled-{compiler}.c'
        run([args.cesta, 'export-c', second, '--std=c11', '--cc', compiler, '-o', other])
        joined = work / f'joined-{compiler}.c'
        joined.write_text(exported.read_text() + other.read_text())
        binary = work / f'joined-{compiler}'
        run([compiler, '-std=c11', '-Wall', '-Wextra', '-Wpedantic', '-Werror', joined, harness, '-o', binary])
        assert run([binary]).stdout == '10 42\n'
        runs += 1

    for name, (source, contracts, message) in NEGATIVE.items():
        path = work / f'{name}.cesta'
        path.write_text(document(source, contracts))
        refused = run([args.cesta, 'export-c', path, '--std=c11', '-o', work / f'{name}.c'], expect=1)
        assert message in refused.stderr, (name, refused.stderr)

    # Every body named: accepted, nothing is admitted as checked, and an
    # unchecked pointer result without an origin stays out of the plan.
    named = work / 'all-named.cesta'
    named.write_text(document('static int *pick(int *a) {\n    return a + 1;\n}\n'
                              'int use(int *a) {\n    return *pick(a);\n}\n',
                              '(unchecked pick (obligation "Returns the next int of an array.")) '
                              '(unchecked use (obligation "Reads the int after a."))'))
    plan = work / 'all-named.plan'
    run([args.cesta, 'export-c', named, '--std=c11', '-o', work / 'all-named.c', '--plan', plan])
    text = plan.read_text()
    assert '(foreign use' in text and '(foreign pick' not in text and '(function ' not in text, text

    grouped = work / 'grouped.cesta'
    grouped.write_text(document(SUM + 'static int last(const int *values, size_t count) {\n    return values[count - 1];\n}\n',
                                '(unchecked (total last) (obligation "Reads count ints at values."))'))
    plan = work / 'grouped.plan'
    run([args.cesta, 'export-c', grouped, '--std=c11', '-o', work / 'grouped.c', '--plan', plan])
    text = plan.read_text()
    assert '(foreign total' in text and '(foreign last' in text and '(function row_sum' in text, text
    duplicate = work / 'grouped-duplicate.cesta'
    duplicate.write_text(document(SUM, '(unchecked (total) (obligation "x")) (unchecked total (obligation "y"))'))
    refused = run([args.cesta, 'export-c', duplicate, '--std=c11', '-o', work / 'dup.c'], expect=1)
    assert 'duplicate function ownership contract' in refused.stderr, refused.stderr

    # An __extension__ definition is read like any other; with no --std the
    # check reads the unit as C11.
    extension = work / 'extension.cesta'
    extension.write_text(document('#include <stdint.h>\n__extension__ static inline uint32_t one(void) {\n    return 1;\n}\n'
                                  'uint32_t two(void) {\n    return one() + one();\n}\n', ''))
    plan = work / 'extension.plan'
    run([args.cesta, 'export-c', extension, '-o', work / 'extension.c', '--plan', plan])
    text = plan.read_text()
    assert '(function one' in text and '(function two' in text, text

    survey = work / 'survey.tsv'
    if survey.exists():
        survey.unlink()
    surveyed = work / 'survey.cesta'
    surveyed.write_text(document(SUM + 'static int *peek(int *p) {\n    return p;\n}\n', ''))
    refused = run([args.cesta, 'export-c', surveyed, '--std=c11', '-o', work / 'survey.c'],
                  env={**os.environ, 'CESTA_OWNERSHIP_SURVEY': str(survey)}, expect=1)
    assert 'ownership survey: 2 of 3 function bodies refused' in refused.stderr, refused.stderr
    rows = [line.split('\t') for line in survey.read_text().splitlines()]
    assert all(row[0].endswith('survey.cesta') for row in rows), rows
    names = [row[1] for row in rows]
    assert names == ['total', 'peek'], names
    print(f'unchecked definitions: {runs} joined builds, {len(NEGATIVE)} refusals, survey of 2 refused bodies')


if __name__ == '__main__':
    main()
