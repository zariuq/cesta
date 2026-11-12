"""Ownership contracts kept beside a header.

NAME.h.ownership applies to every unit that reads NAME.h. The unit that
defines a function is checked against the same text its callers assume, a
native helper defined in the header is named unchecked once, and a contract
error is reported at its own line of the interface file.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ownership_core import ROOT, BINARY

HEADER = '''#ifndef READER_H
#define READER_H
#include <stdint.h>
typedef struct { const uint8_t *bytes; uint32_t length; uint32_t at; } Reader;
typedef struct Hidden Hidden;
static inline void fence(uint32_t value) { __asm__ volatile ("" : : "r"(value) : "memory"); }
const uint8_t *reader_peek(const Reader *r);
const uint32_t *reader_position(const Reader *r);
void reader_step(Reader *r);
void reader_reset(Reader *r);
#ifdef READER_EXTRA
typedef struct { const Reader *of; } Extra;
uint32_t reader_left(const Reader *r);
#endif
#endif
'''
INTERFACE = '''(cesta ownership-interface/1
  (unchecked fence
    (obligation "An empty instruction sequence that reads its scalar operand; it touches no C object."))
  (record Reader (field bytes (borrow read)))
  (record Hidden (field next (borrow read)))
  (record Extra (field of (borrow read)))
  (function reader_peek (returns-borrow 0 bytes nullable))
  (function reader_position (returns-borrow 0 at))
  (function reader_step)
  (function reader_left))
'''
DEFINER = '''const uint8_t *reader_peek(const Reader *r) { if (r->at < r->length) return r->bytes; return 0; }
const uint32_t *reader_position(const Reader *r) { return &r->at; }
void reader_step(Reader *r) { if (r->at < r->length) r->at += 1; fence(r->at); }
'''
EXTRA = '''#ifdef READER_EXTRA
uint32_t reader_left(const Reader *r) { return r->length - r->at; }
#endif
'''
CLIENT = '''uint32_t drain(Reader *r) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < 8; ++i) {
        const uint8_t *p = reader_peek(r);
        if (!p) break;
        sum += *p; fence(sum);
        reader_step(r);
    }
#ifdef READER_EXTRA
    sum += reader_left(r);
#endif
    return sum + *reader_position(r);
}
int main(void) { static const uint8_t data[3] = {4,5,6}; Reader r = {data,3,0}; return drain(&r) != 15; }
'''


def unit(body, contracts='', include='reader.h'):
    return ('(cesta native-c/1 compact-c/2\n  (unit\n    (ownership (check-all)' +
            ''.join('\n      ' + line for line in contracts.splitlines()) +
            ')\n    (include "' + include + '")\n    (native ' + json.dumps(body) + ')))\n')


def run(args, accepted=True, cwd=None):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, cwd=cwd)
    assert (result.returncode == 0) == accepted, (args, result.returncode, result.stderr[-4000:])
    return result


def tree(work, name, files):
    """One directory per case: the shared header and interface, then overrides."""
    directory = work / name
    directory.mkdir()
    for path, text in {'reader.h': HEADER, 'reader.h.ownership': INTERFACE, **files}.items():
        if text is not None:
            (directory / path).write_text(text)
    return directory


def line_of(text, needle):
    return text[:text.index(needle)].count('\n') + 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--admit', action='store_true')
    parser.add_argument('--cesta', type=Path, default=BINARY)
    parser.add_argument('--lean-root', type=Path, default=ROOT.parent / 'Mettapedia/lean/mettapedia')
    args = parser.parse_args()
    cesta = args.cesta.resolve()
    work = Path(tempfile.mkdtemp(prefix='ownership-interfaces-', dir=ROOT / 'build'))
    flags = ['-std=c11', '-O2', '-g0', '-Wall', '-Wextra', '-Werror']
    plans = []

    # Two units share one header. Neither restates a contract: the definer's
    # bodies are checked against the interface, the client calls under it.
    programs = {
        'shared': ({}, [], ''),
        'extra_configuration': ({}, ['-DREADER_EXTRA'], ''),
        # A contract restated in the unit, word for word, is the same contract.
        'restated': ({}, [], '(function reader_step)'),
        # The interface of a header read through another header applies too.
        'nested': ({'outer.h': '#include "reader.h"\n'}, [], ''),
    }
    executions = 0
    for name, (files, defines, restated) in programs.items():
        directory = tree(work, name, files)
        include = 'outer.h' if 'outer.h' in files else 'reader.h'
        bodies = {'reader': DEFINER + EXTRA, 'client': CLIENT}
        for stem, body in bodies.items():
            (directory / (stem + '.cesta')).write_text(unit(body, restated if stem == 'reader' else '', include))
            (directory / (stem + '-reference.c')).write_text(f'#include "{include}"\n' + body)
        for cc in ('gcc', 'clang'):
            for stem in bodies:
                exported = directory / f'{stem}-{cc}.c'
                plan = directory / f'{stem}-{cc}.plan'
                run([cesta, 'export-c', '--cc', cc, '-o', exported, '--plan', plan, directory / (stem + '.cesta'),
                     '--', '-std=c11', f'-I{directory}', *defines])
                plans.append(plan)
                for variant, source in (('export', exported), ('reference', directory / (stem + '-reference.c'))):
                    obj = directory / f'{stem}-{cc}-{variant}.o'
                    run([cc, *flags, f'-I{directory}', *defines, '-c', source, '-o', obj])
                    run(['objcopy', '-O', 'binary', '--only-section=.text', obj, obj.with_suffix('.text')])
                assert (directory / f'{stem}-{cc}-export.text').read_bytes() == \
                    (directory / f'{stem}-{cc}-reference.text').read_bytes(), (name, stem, cc)
            for sanitized in (False, True):
                binary = directory / f'program-{cc}-{sanitized}'
                run([cc, *flags, f'-I{directory}', *defines,
                     *(['-fsanitize=address,undefined', '-fno-pie', '-no-pie'] if sanitized else []),
                     directory / f'reader-{cc}.c', directory / f'client-{cc}.c', '-o', binary])
                run([binary])
                executions += 1
        client_plan = (directory / 'client-gcc.plan').read_text()
        assert '(foreign reader_peek ' in client_plan and 'meets the contract stated in reader.h.ownership' in client_plan
        assert client_plan.count('it touches no C object.') == 1, client_plan
        definer_plan = (directory / 'reader-gcc.plan').read_text()
        assert '(foreign reader_peek ' not in definer_plan and 'reader_peek' in definer_plan
        assert ('reader_left' in client_plan) == bool(defines)

    # The unit depends on the interface file, and its fingerprint covers it.
    directory = tree(work, 'dependency', {})
    (directory / 'client.cesta').write_text(unit(CLIENT))
    run([cesta, 'cc', '--cc', 'gcc', '-std=c11', f'-I{directory}', '-MD', '-c', directory / 'client.cesta',
         '-o', directory / 'client.o'])
    assert 'reader.h.ownership' in (directory / 'client.d').read_text()
    fingerprint = lambda plan: re.search(r'\(sha256 "([0-9a-f]{64})"\)', plan.read_text()).group(1)
    export = lambda plan: run([cesta, 'export-c', '-o', directory / 'pinned.c', '--plan', directory / plan,
                               directory / 'pinned.cesta', '--', '-std=c11', f'-I{directory}'])
    check = lambda accepted: run([cesta, 'cc', '--cc', 'gcc', '-std=c11', f'-I{directory}', '-fsyntax-only',
                                  directory / 'pinned.cesta'], accepted=accepted)
    plain = unit(CLIENT)
    (directory / 'pinned.cesta').write_text(plain)
    export('a.plan')
    # The pin stays on the declaration's line, so the native text keeps its lines.
    pinned = plain.replace('(ownership (check-all))', f'(ownership (check-all) (abi-fingerprint "{fingerprint(directory / "a.plan")}"))')
    (directory / 'pinned.cesta').write_text(pinned)
    check(True)
    (directory / 'reader.h.ownership').write_text(INTERFACE.replace('it touches no C object', 'it reads no C object'))
    changed = check(False)
    assert 'ABI fingerprint differs' in changed.stderr, changed.stderr
    (directory / 'pinned.cesta').write_text(plain)
    export('b.plan')
    assert fingerprint(directory / 'a.plan') != fingerprint(directory / 'b.plan')

    # Refusals: (files, unit body, unit contracts, needle, located)
    position_after_step = '''uint32_t late(Reader *r) { const uint32_t *p = reader_position(r); reader_step(r); return *p; }
'''
    negative = {
        # The definer is held to the published contract.
        'definer_returns_other_storage': ({}, DEFINER.replace('return r->bytes;', 'static const uint8_t zero; return &zero;'),
                                          '', 'ownership', None),
        'definer_omits_null_contract': ({'reader.h.ownership': INTERFACE.replace(' nullable))', '))')}, DEFINER,
                                        '', 'null return requires a nullable result contract', None),
        # A caller is held to it too.
        'caller_keeps_loan_across_write': ({}, position_after_step, '', 'ownership', None),
        'caller_ignores_null': ({}, 'uint8_t first(const Reader *r) { return *reader_peek(r); }\n', '', 'ownership', None),
        # One contract per function: a unit cannot state a different one.
        'unit_states_another_contract': ({}, DEFINER, '(function reader_peek (returns-borrow 0 bytes))',
                                         'duplicate function ownership contract (reader.h.ownership:7)', None),
        'unit_unchecks_a_contracted_body': ({}, DEFINER, '(unchecked reader_step (obligation "Not read."))',
                                            'duplicate function ownership contract (reader.h.ownership:9)', None),
        'two_interfaces_disagree': ({'again.h': '#include "reader.h"\nvoid reader_step(Reader *r);\n',
                                     'again.h.ownership': '(cesta ownership-interface/1\n'
                                                          '  (unchecked reader_step (obligation "Stated twice.")))\n'},
                                    '#include "again.h"\n' + DEFINER, '',
                                    'duplicate function ownership contract (again.h.ownership:2)', None),
        # An interface holds contracts only, and its errors name its own lines.
        'interface_check_all': ({'reader.h.ownership': INTERFACE.replace('  (function reader_step)', '  (check-all)\n  (function reader_step)')},
                                CLIENT, '', 'an interface holds contracts; check-all, abi-fingerprint and global belong to a unit (reader.h.ownership:9)', None),
        'interface_global': ({'reader.h.ownership': INTERFACE.replace('  (function reader_step)', '  (global state reader_step)\n  (function reader_step)')},
                             CLIENT, '', 'belong to a unit (reader.h.ownership:9)', None),
        'interface_unbalanced': ({'reader.h.ownership': INTERFACE.rstrip()[:-1] + '\n'}, CLIENT, '', '(reader.h.ownership:', None),
        'interface_other_document': ({'reader.h.ownership': '(cesta native-c/1 compact-c/2 (unit))\n'}, CLIENT, '',
                                     'expected (cesta ownership-interface/1 CONTRACT...) (reader.h.ownership:1)', None),
        'interface_unknown_field': ({'reader.h.ownership': INTERFACE.replace('(field bytes (borrow read))', '(field missing (borrow read))')},
                                    CLIENT, '', 'field contract must name a pointer member (reader.h.ownership:4)', None),
        'interface_unknown_part': ({'reader.h.ownership': INTERFACE.replace('(function reader_step)', '(function reader_step (keeps 0))')},
                                   CLIENT, '', 'unknown or malformed function ownership contract (reader.h.ownership:9)', None),
        'interface_foreign_but_defined': ({'reader.h.ownership': INTERFACE.replace('(function reader_step)', '(foreign reader_step (obligation "Elsewhere."))')},
                                          DEFINER, '', 'a defined function must be checked, or named unchecked with its obligation (reader.h.ownership:9)', None),
        # Nothing is trusted silently.
        'no_interface': ({'reader.h.ownership': None}, CLIENT, '', 'ownership', None),
        'call_without_contract': ({}, 'void again(Reader *r) { reader_reset(r); }\n', '',
                                  'call requires a checked body or an explicit foreign obligation', None),
        # A unit's own record contract still needs the complete type.
        'unit_record_of_incomplete_type': ({}, CLIENT, '(record Hidden (field next (borrow read)))',
                                           'record requires a distinct complete structure typedef', 'unit'),
    }
    for name, (files, body, contracts, needle, located) in negative.items():
        directory = tree(work, name, files)
        source = directory / 'unit.cesta'
        text = unit(body, contracts)
        source.write_text(text)
        for cc in ('gcc', 'clang'):
            result = run([cesta, 'cc', '--cc', cc, '-std=c11', f'-I{directory}', '-fsyntax-only', source], accepted=False)
            assert needle in result.stderr, (name, cc, result.stderr)
            if located == 'unit':
                # A contract error in the unit is placed at its own item.
                line = line_of(text, contracts.splitlines()[0])
                assert f'unit.cesta:{line}:7: error: ownership: ' in result.stderr, (name, line, result.stderr)

    # A project build re-checks a unit when an interface file appears beside
    # a header it reads, and again whenever that file changes.
    project = work / 'project'
    (project / 'src').mkdir(parents=True)
    (project / 'include').mkdir()
    (project / 'cesta.project').write_text('(cesta project/1 (out "out")\n  (profile hosted (flags "-std=c11" "-O2"))\n'
        '  (target box (profile hosted) (kind archive) (sources "src/*.cesta") (include "include")))\n')
    (project / 'include/box.h').write_text('typedef struct { int value; } Box;\nconst int *box_value(const Box *b);\n')
    stated = '(function box_value (returns-borrow 0 value))'
    (project / 'src/box.cesta').write_text(unit('const int *box_value(const Box *b) { return &b->value; }\n', stated, 'box.h'))
    build = lambda accepted: run([cesta, 'build', '--cc', 'gcc', '-j', '2', 'box'], accepted=accepted, cwd=project)
    interface = project / 'include/box.h.ownership'
    build(True)
    build(True)
    for round in range(2):
        interface.write_text('(cesta ownership-interface/1\n  (function box_value (returns-static)))\n')
        refused = build(False)
        assert 'duplicate function ownership contract (box.h.ownership:2)' in refused.stderr, refused.stderr
        interface.write_text('(cesta ownership-interface/1\n  ' + stated + ')\n')
        build(True)

    if args.admit:
        from ownership_admission import admit_record
        from oracles.pro_compact_cesta import parse
        for plan in plans:
            record, = parse(plan.read_bytes())
            admit_record(record, plan.with_suffix('.lean'), args.lean_root)
    shutil.rmtree(work)
    print(f'interfaces: {len(programs)} two-unit programs, {executions} executions, {len(plans)} plans, '
          f'{len(negative)} source refusals; GCC/Clang, sanitizers, exact exported instructions, '
          f'dependency and fingerprint coverage, project rebuilds' + ('; emitted plans admitted' if args.admit else ''), flush=True)


if __name__ == '__main__':
    main()
