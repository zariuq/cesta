"""A stronger nonnull result implements the same nullable import contract.

All argument requirements and other result guarantees stay exact. The two
units share ordinary C declarations and state their own ownership contracts.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from ownership_core import ROOT, BINARY
from oracles.pro_compact_cesta import Sym, Text, parse


def unit(body, contracts):
    return ('(cesta native-c/1 compact-c/2 (unit (ownership (check-all) ' + contracts +
            ') (include "api.h") (native ' + json.dumps(body) + ')))\n')


def run(command, accepted=True, cwd=ROOT):
    result = subprocess.run(list(map(str, command)), cwd=cwd, text=True, capture_output=True)
    assert (result.returncode == 0) == accepted, (command, result.returncode, result.stdout, result.stderr)
    return result


def dump(value):
    if isinstance(value, list):
        return '(' + ' '.join(map(dump, value)) + ')'
    if isinstance(value, Text):
        return json.dumps(value.data.decode())
    return str(value)


def section(node, name):
    return next(child for child in node if isinstance(child, list) and child and child[0] == name)


CASES = {
    'borrowed': (
        'const uint32_t *pick(const uint32_t *p);\n',
        'const uint32_t *pick(const uint32_t *p) { return p; }\n',
        '(function pick (returns-borrow 0))',
        '(foreign pick (returns-borrow 0 nullable) (obligation "The provider lends its input."))',
        'int main(void) { const uint32_t value = 7; const uint32_t *p = pick(&value); '
        'if (!p) return 1; return *p != 7; }\n'),
    'static': (
        'const uint32_t *pick(void);\n',
        'static const uint32_t value = 7;\nconst uint32_t *pick(void) { return &value; }\n',
        '(function pick (returns-static))',
        '(foreign pick (returns-static nullable) (obligation "The provider returns static storage."))',
        'int main(void) { const uint32_t *p = pick(); if (!p) return 1; return *p != 7; }\n'),
    'owned': (
        'uint32_t *pick(uint32_t *p);\n',
        'uint32_t *pick(uint32_t *p) { return p; }\n',
        '(allocator Heap malloc free (obligation "Standard allocation family.")) '
        '(function pick (owns 0 Heap) (returns-own Heap))',
        '(allocator Heap malloc free (obligation "Standard allocation family.")) '
        '(foreign pick (owns 0 Heap) (returns-own Heap nullable) '
        '(obligation "The provider consumes the input and transfers its returned owner."))',
        'int main(void) { uint32_t *p = malloc(sizeof(uint32_t)); if (!p) { free(p); return 1; } *p = 7; '
        'uint32_t *q = pick(p); if (!q) { free(q); return 1; } int wrong = *q != 7; free(q); return wrong; }\n'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cesta', type=Path, default=BINARY)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--admit', action='store_true')
    parser.add_argument('--lean-root', type=Path, default=ROOT.parent / 'Mettapedia/lean/mettapedia')
    args = parser.parse_args()
    cesta = args.cesta.resolve()
    work = args.work.resolve() if args.work else Path(tempfile.mkdtemp(prefix='ownership-refinement-', dir=ROOT / 'build'))
    work.mkdir(parents=True, exist_ok=True)
    programs = plans = 0
    compositions = []
    for name, (prototype, provider, provided, expected, caller) in CASES.items():
        for cc in ('gcc', 'clang'):
            directory = work / f'{name}-{cc}'
            directory.mkdir()
            header = '#include <stdint.h>\n#include <stddef.h>\n' + prototype
            if name == 'owned':
                header += 'void *malloc(size_t);\nvoid free(void *);\n'
                header += 'void *spare_reserve(size_t);\nvoid spare_release(void *);\n'
            (directory / 'api.h').write_text(header)
            provider_contract = provided
            if name == 'owned':
                # The same public family has different local numeric IDs.
                # Composition must use its name, not accidental numbering.
                provider_contract = ('(allocator Spare spare_reserve spare_release '
                                     '(obligation "A distinct unused allocation family.")) ' + provided)
            (directory / 'provider.cesta').write_text(unit(provider, provider_contract))
            (directory / 'caller.cesta').write_text(unit(caller, expected))
            records = []
            for stem in ('provider', 'caller'):
                source = directory / (stem + '.cesta')
                plan, record = directory / (stem + '.plan'), directory / (stem + '.contracts')
                run([cesta, 'export-c', '--cc', cc, '--plan', plan, '-o', directory / (stem + '.c'),
                     source, '--', '-std=c11', '-I' + str(directory)])
                run([cesta, 'cc', '--cc', cc, '--contracts', record, '-std=c11', '-O2',
                     '-I' + str(directory), '-c', source, '-o', directory / (stem + '.o')])
                records.append(record)
                plans += 1
            result = run([cesta, 'link-check', '--obligations', directory / 'app.obligations', *records])
            assert '1 cross-unit calls meet checked definitions' in result.stderr, result.stderr
            compositions.append((records, [directory / 'provider.plan', directory / 'caller.plan'], directory))
            for sanitized in (False, True):
                binary = directory / f'app-{sanitized}'
                run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
                     *(['-fsanitize=address,undefined', '-fno-pie', '-no-pie'] if sanitized else []),
                     directory / 'provider.c', directory / 'caller.c', '-o', binary])
                run([binary])
                programs += 1

            # Reverse direction: a nullable definition cannot satisfy a nonnull
            # caller. Mutate the actual emitted records without executing UB.
            provider_record, = parse(records[0].read_bytes())
            caller_record, = parse(records[1].read_bytes())
            defined = next(item for item in provider_record[2][4:] if item[0] == 'define' and item[1] == 'pick')
            assumed = next(item for item in caller_record[2][4:] if item[0] == 'assume' and item[1] == 'pick')
            p_result = section(section(defined, 'contract'), 'signature')[3]
            c_result = section(section(assumed, 'contract'), 'signature')[3]
            assert p_result[-1] == 'nonnull' and c_result[-1] == 'nullable'
            p_result[-1], c_result[-1] = Sym('nullable'), Sym('nonnull')
            changed_provider, changed_caller = directory / 'reverse-provider.contracts', directory / 'reverse-caller.contracts'
            changed_provider.write_text(dump(provider_record) + '\n')
            changed_caller.write_text(dump(caller_record) + '\n')
            refused = run([cesta, 'link-check', changed_provider, changed_caller], accepted=False)
            assert 'ownership contract' in refused.stderr

            # Result origin/extent/family and argument requirements are not
            # weakened by this refinement rule.
            for mutation in ('origin', 'argument', 'callback', 'layout'):
                original, = parse(records[0].read_bytes())
                definition = next(item for item in original[2][4:] if item[0] == 'define' and item[1] == 'pick')
                contract = section(definition, 'contract')
                signature = section(contract, 'signature')
                if mutation == 'origin':
                    signature[3] = [Sym('result'), Sym('unadmitted')]
                elif mutation == 'argument':
                    signature[1].append([Sym('borrowed'), Sym('1'), Sym('write')])
                elif mutation == 'callback':
                    section(contract, 'callbacks').append([Sym('callback'), Text(b'Changed')])
                else:
                    # Both complete views of a named record must still agree.
                    section(contract, 'records').append([Sym('struct'), Text(b'Probe'), Sym('8')])
                    incoming, = parse(records[1].read_bytes())
                    assumption = next(item for item in incoming[2][4:] if item[0] == 'assume' and item[1] == 'pick')
                    section(section(assumption, 'contract'), 'records').append([Sym('struct'), Text(b'Probe'), Sym('16')])
                    changed_caller.write_text(dump(incoming) + '\n')
                changed_provider.write_text(dump(original) + '\n')
                run([cesta, 'link-check', changed_provider,
                     changed_caller if mutation == 'layout' else records[1]], accepted=False)
    if args.admit:
        from ownership_composition import qualify
        for records, emitted, directory in compositions:
            qualify(records, emitted, directory / 'composition', args.lean_root.resolve())
    print(f'refinement: {len(CASES)} result modes, {programs} executions, {plans} actual plans; '
          'GCC/Clang and sanitizers; reverse, origin, argument, callback and layout refusals' +
          ('; kernel-admitted body plans and link composition' if args.admit else ''), flush=True)
    if not args.work:
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
