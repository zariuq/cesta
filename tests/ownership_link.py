"""Separately checked units compose at link time.

Every call a checked body makes into another unit is matched with the
definition the program links. It composes when that definition was checked
under the same ownership contract, C type, callback contracts and record
layouts; a definition left unchecked, or none in Cesta, stays a listed
obligation; a difference is an error at the call.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ownership_core import ROOT, BINARY
from ownership_interfaces import HEADER, INTERFACE, DEFINER, EXTRA, CLIENT, unit, line_of

RESET = 'void reader_reset(Reader *r) { r->at = 0; }\n'
LOG_HEADER = '#include <stdint.h>\nvoid log_value(uint32_t value);\n'
LOG_INTERFACE = '(cesta ownership-interface/1\n  (foreign log_value (obligation "Writes the value to standard error.")))\n'
LOG_C = '#include <stdio.h>\n#include <stdint.h>\nvoid log_value(uint32_t value) { fprintf(stderr, "%u\\n", (unsigned)value); }\n'
CALLER = CLIENT.replace('uint32_t drain(Reader *r) {\n', 'uint32_t drain(Reader *r) {\n    reader_reset(r);\n') \
               .replace('    return sum + *reader_position(r);', '    log_value(sum);\n    return sum + *reader_position(r);')
assert CALLER.count('reader_reset(r);') == 1 and CALLER.count('log_value(sum);') == 1
PAIR = ('#include <stdint.h>\nstruct pair { uint32_t a; uint64_t b; };\n'
        'uint64_t pair_sum(const struct pair *p);\nvoid reader_skip(uint32_t *at, uint32_t by);\n')


def run(args, accepted=True, cwd=None):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, cwd=cwd)
    assert (result.returncode == 0) == accepted, (args, result.returncode, result.stdout[-2000:], result.stderr[-4000:])
    return result


def project(work, name, files, targets):
    directory = work / name
    for path, text in files.items():
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_text(text)
    (directory / 'cesta.project').write_text(
        '(cesta project/1 (out "out")\n  (profile hosted (flags "-std=c11" "-O2" "-Wall" "-Werror"))\n' + targets + ')\n')
    return directory


def reader_files(definer_contracts='', interface=INTERFACE, client=CLIENT, extra=None):
    files = {'include/reader.h': HEADER, 'include/reader.h.ownership': interface,
             'src/reader.cesta': unit(DEFINER + EXTRA, definer_contracts),
             'src/client.cesta': unit(client)}
    files.update(extra or {})
    return {k: v for k, v in files.items() if v is not None}


APP = '  (target app (profile hosted) (sources "src/*.cesta") (include "include"))\n'


def recorded_line(directory, body, anchor, needle):
    """The line a record gives to a line of a native body: the recorded line
    of a definition in the same body, plus the distance between the two."""
    record = (directory / 'out/app/obj/src/client.cesta.o.contracts').read_text()
    name = re.match(r'\w+ \**(\w+)\(', anchor).group(1)
    at = re.search(r'\(define ' + name + r' \w+ \w+ \(at "src/client.cesta" (\d+)\)', record)
    assert at, record
    return int(at.group(1)) + line_of(body, needle) - line_of(body, anchor)


def summary(result):
    found = re.search(r'contracts: (\d+) units; (\d+) calls into other units meet checked definitions; '
                      r'(\d+) native and (\d+) unchecked functions remain obligations; (\d+) calls differ', result.stderr)
    assert found, result.stderr
    return tuple(map(int, found.groups()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cesta', type=Path, default=BINARY)
    args = parser.parse_args()
    cesta = args.cesta.resolve()
    work = Path(tempfile.mkdtemp(prefix='ownership-link-', dir=ROOT / 'build'))
    build = lambda directory, *extra, accepted=True, cc='gcc': run(
        [cesta, 'build', '--cc', cc, '-j', '2', *extra], accepted=accepted, cwd=directory)
    composed = programs = 0

    # Two units, one interface: each call reaches a definition checked under
    # the contract the caller assumed. The records do not depend on the compiler.
    records = {}
    for cc in ('gcc', 'clang'):
        directory = project(work, f'composed-{cc}', reader_files(), APP)
        result = build(directory, cc=cc)
        assert summary(result) == (2, 3, 0, 1, 0), result.stderr
        run([directory / 'out/app/app'])
        obligations = (directory / 'out/app/app.obligations').read_text()
        assert '(checked-calls 3)' in obligations and '(unchecked fence' in obligations, obligations
        assert '(native' not in obligations, obligations
        records[cc] = {path.name: path.read_text() for path in (directory / 'out/app/obj/src').glob('*.contracts')}
        assert set(records[cc]) == {'reader.cesta.o.contracts', 'client.cesta.o.contracts'}, records[cc]
        # Nothing changed: nothing is compiled, linked or checked again.
        again = build(directory, cc=cc)
        assert '0 compiled, 2 up to date; current' in again.stderr and 'contracts:' not in again.stderr, again.stderr
        # The same check without the project.
        check = run([cesta, 'link-check', '--program', 'app', '--obligations', directory / 'standalone.obligations',
                     *sorted((directory / 'out/app/obj/src').glob('*.contracts'))])
        assert '2 units; 3 cross-unit calls meet checked definitions; 0 native and 1 unchecked functions remain obligations; 0 calls differ' in check.stderr, check.stderr
        composed += 3; programs += 1
    assert records['gcc'] == records['clang'], 'link records differ between compilers'

    # An interface written from the unit that defines the header's functions:
    # its own contracts, the default for a checked body that states none, and
    # an unchecked group name by name. Written again, it is the same text, and
    # a client that reads it composes with the definer.
    stated = ('(record Reader (field bytes (borrow read)))\n(function reader_peek (returns-borrow 0 bytes nullable))\n'
              '(function reader_position (returns-borrow 0 at))\n'
              '(unchecked (fence) (obligation "An empty instruction sequence."))')
    directory = project(work, 'exported', {'include/reader.h': HEADER, 'src/reader.cesta': unit(DEFINER, stated),
                                           'src/client.cesta': unit(CLIENT)}, APP)
    interface = directory / 'include/reader.h.ownership'
    export = lambda: run([cesta, 'export-interface', '--cc', 'gcc', '-o', interface, 'include/reader.h',
                          'src/reader.cesta', '--', '-std=c11', '-Iinclude'], cwd=directory)
    export()
    written = interface.read_text()
    assert written == ('(cesta ownership-interface/1\n  (record Reader (field bytes (borrow read)))\n'
                       '  (unchecked fence (obligation "An empty instruction sequence."))\n'
                       '  (function reader_peek (returns-borrow 0 bytes nullable))\n'
                       '  (function reader_position (returns-borrow 0 at))\n  (function reader_step)\n)\n'), written
    export()
    assert interface.read_text() == written
    result = build(directory)
    assert summary(result) == (2, 3, 0, 1, 0), result.stderr
    run([directory / 'out/app/app'])
    (directory / 'other').mkdir()
    (directory / 'other/plain.cesta').write_text('(cesta native-c/1 compact-c/2\n  (unit\n    (include "reader.h")\n'
                                                 '    (native "void reader_reset(Reader *r) { r->at = 0; }")))\n')
    refused = run([cesta, 'export-interface', '--cc', 'gcc', 'include/reader.h', 'other/plain.cesta', '--', '-std=c11',
                   '-Iinclude'], accepted=False, cwd=directory)
    assert 'the unit has no ownership declaration' in refused.stderr, refused.stderr
    composed += 3; programs += 1

    # An archive carries its members' records to the program that links it.
    directory = project(work, 'archive', reader_files(),
        '  (target reader (profile hosted) (kind archive) (sources "src/reader.cesta") (include "include"))\n'
        '  (target app (profile hosted) (sources "src/client.cesta") (include "include") (link "out/reader/libreader.a"))\n')
    result = build(directory, 'app')
    assert summary(result) == (2, 3, 0, 1, 0), result.stderr
    assert (directory / 'out/reader/libreader.a.contracts').read_text().count('(unit ') == 1
    run([directory / 'out/app/app'])
    composed += 3; programs += 1

    # Obligations stay listed: a call into plain C, and one into a body left unchecked.
    interface = INTERFACE.replace('  (function reader_left))',
        '  (function reader_left)\n  (unchecked reader_reset (obligation "Sets the position to zero; touches nothing else.")))')
    directory = project(work, 'obligations', reader_files(
        interface=interface, client='#include "log.h"\n' + CALLER,
        extra={'src/reader.cesta': unit(DEFINER + EXTRA + RESET), 'include/log.h': LOG_HEADER,
               'include/log.h.ownership': LOG_INTERFACE, 'src/log.c': LOG_C}),
        APP.replace('"src/*.cesta"', '"src/*.cesta" "src/*.c"'))
    result = build(directory)
    assert summary(result) == (2, 3, 1, 2, 0), result.stderr
    obligations = (directory / 'out/app/app.obligations').read_text()
    body = '#include "log.h"\n' + CALLER
    log_line = recorded_line(directory, body, 'uint32_t drain(', 'log_value(sum)')
    reset_line = recorded_line(directory, body, 'uint32_t drain(', 'reader_reset(r)')
    assert f'(native log_value\n  (assumed "src/client.cesta" {log_line} "Writes the value to standard error.")' in obligations, obligations
    assert '(unchecked reader_reset\n  (defined "src/reader.cesta"' in obligations, obligations
    assert f'(assumed "src/client.cesta" {reset_line} "Sets the position to zero; touches nothing else.")' in obligations, obligations
    run([directory / 'out/app/app'])
    programs += 1

    # A call that assumes another contract than an unchecked definition
    # states is reported, and rests on its own obligation, listed as differing.
    resetting = CLIENT.replace('uint32_t drain(Reader *r) {\n', 'uint32_t drain(Reader *r) {\n    reader_reset(r);\n')
    directory = project(work, 'unchecked_differs', reader_files(extra={
        'src/reader.cesta': unit(DEFINER + EXTRA + RESET, '(unchecked reader_reset (obligation "Sets the position to zero."))'),
        'src/client.cesta': unit(resetting, '(foreign reader_reset (nullable 0) (obligation "Resets a reader; ignores null."))')}), APP)
    result = build(directory)
    line = recorded_line(directory, resetting, 'uint32_t drain(', 'reader_reset(r)')
    assert (f'src/client.cesta:{line}: warning: reader_reset is called under another ownership contract than its '
            f'unchecked definition at src/reader.cesta:') in result.stderr, result.stderr
    assert 'the call rests on its own obligation' in result.stderr and 'optional-borrowed 16 write' in result.stderr
    assert summary(result) == (2, 3, 0, 2, 1), result.stderr
    obligations = (directory / 'out/app/app.obligations').read_text()
    assert f'(assumed "src/client.cesta" {line} "Resets a reader; ignores null." (differs "ownership contract"))' in obligations, obligations
    run([directory / 'out/app/app'])
    programs += 1

    # Refusals: a definition checked apart from the header its callers read.
    escaped = HEADER.replace('READER_H', 'READER_IMPL_H')
    own = '(record Reader (field bytes (borrow read)))\n(function reader_peek (returns-borrow 0 bytes nullable))\n' \
          '(function reader_position (returns-borrow 0 at))\n(function reader_step)\n' \
          '(unchecked fence (obligation "An empty instruction sequence."))'
    pairs = '(cesta ownership-interface/1\n  (function reader_skip)\n  (function pair_sum))\n'
    cases = {
        # The callers read that the result is never null; the definer, checked
        # apart from their header, may return null.
        'ownership_contract': ({'include/reader.h.ownership': INTERFACE.replace(' nullable))', '))'),
                                'include/reader_impl.h': escaped,
                                'src/reader.cesta': unit(DEFINER, own, 'reader_impl.h')},
                               'reader_peek is called under another ownership contract than its checked definition at src/reader.cesta:',
                               'nullable', 'uint32_t drain(', 'reader_peek(r)'),
        # The same ownership, another C type.
        'c_type': ({'include/pair.h': PAIR, 'include/pair.h.ownership': pairs,
                    'include/pair_impl.h': PAIR.replace('uint32_t by', 'uint64_t by'),
                    'src/reader.cesta': unit('void reader_skip(uint32_t *at, uint64_t by) { *at += (uint32_t)by; }\n', '', 'pair_impl.h'),
                    'src/client.cesta': unit('uint32_t skip(uint32_t at) { reader_skip(&at, 2); return at; }\n'
                                             'int main(void) { return skip(1) != 3; }\n', '', 'pair.h')},
                   'reader_skip is called under another C type than its checked definition at src/reader.cesta:',
                   'uint', 'uint32_t skip(', 'reader_skip(&at'),
        # The same declaration, records laid out differently.
        'record_layout': ({'include/pair.h': PAIR, 'include/pair.h.ownership': pairs,
                           'include/pair_impl.h': PAIR.replace('uint32_t a; uint64_t b;', 'uint64_t b; uint32_t a;'),
                           'src/reader.cesta': unit('uint64_t pair_sum(const struct pair *p) { return p->a + p->b; }\n', '', 'pair_impl.h'),
                           'src/client.cesta': unit('uint64_t total(void) { struct pair p = {1, 2}; return pair_sum(&p); }\n'
                                                    'int main(void) { return total() != 3; }\n', '', 'pair.h')},
                          'pair_sum is called under another record layout than its checked definition at src/reader.cesta:',
                          'field', 'uint64_t total(', 'pair_sum(&p)'),
    }
    for name, (overrides, needle, shown, anchor, call) in cases.items():
        files = reader_files(extra=overrides)
        if name != 'ownership_contract':
            files = {k: v for k, v in files.items() if not k.startswith('include/reader')}
        directory = project(work, name, files, APP)
        refused = build(directory, accepted=False)
        client = json.loads(re.search(r'\(native (".*")\)\)\)', (directory / 'src/client.cesta').read_text()).group(1))
        line = recorded_line(directory, client, anchor, call)
        assert f'src/client.cesta:{line}: error: {needle}' in refused.stderr, (name, refused.stderr)
        assert '  call:' in refused.stderr and '  definition:' in refused.stderr and shown in refused.stderr, refused.stderr
        assert 'disagree' in refused.stderr and not (directory / 'out/app/app').exists(), refused.stderr
        # Refused again on the next build: a failed check leaves nothing current.
        build(directory, accepted=False)

    # One program, one target. (An x86 unit compiled with -funsigned-char is
    # already refused when it is checked, so the record is edited here.)
    target = work / 'target'
    target.mkdir()
    composed_records = sorted((work / 'composed-gcc/out/app/obj/src').glob('*.contracts'))
    for edit, shown in (('(abi "x86_64-sysv-lp64")', '(abi "i386-sysv")'), ('(char-signed 1)', '(char-signed 0)')):
        other = target / ('other-' + shown.split()[0][1:] + '.contracts')
        text = composed_records[1].read_text()
        assert edit in text, text
        other.write_text(text.replace(edit, shown))
        refused = run([cesta, 'link-check', composed_records[0], other], accepted=False)
        assert 'src/reader.cesta: error: compiled for another target than src/client.cesta' in refused.stderr, refused.stderr
        assert shown in refused.stderr, refused.stderr

    # Freshness: an interface that appears beside a header a checked unit
    # reads is a reason to check the unit again, whatever its timestamp. A
    # unit not checked for ownership does not read interfaces and is not
    # rebuilt for one.
    files = {'include/box.h': 'typedef struct { int value; } Box;\nconst int *box_value(const Box *b);\nint box_twice(int v);\n',
             'src/box.cesta': unit('const int *box_value(const Box *b) { return &b->value; }\n',
                                   '(function box_value (returns-borrow 0 value))', 'box.h'),
             'src/plain.cesta': '(cesta native-c/1 compact-c/2\n  (unit\n    (include "box.h")\n'
                                '    (native "int box_twice(int v) { return 2 * v; }")))\n'}
    directory = project(work, 'fresh', files,
        '  (target box (profile hosted) (kind archive) (sources "src/*.cesta") (include "include"))\n')
    build(directory)
    obj = directory / 'out/box/obj/src'
    assert (obj / 'box.cesta.o.contracts').exists() and not (obj / 'plain.cesta.o.contracts').exists()
    interface = directory / 'include/box.h.ownership'
    interface.write_text('(cesta ownership-interface/1\n  (function box_value (returns-static)))\n')
    past = (obj / 'box.cesta.o').stat().st_mtime - 3600
    os.utime(interface, (past, past))
    refused = build(directory, accepted=False)
    assert 'duplicate function ownership contract (box.h.ownership:2)' in refused.stderr, refused.stderr
    interface.write_text('(cesta ownership-interface/1\n  (function box_value (returns-borrow 0 value)))\n')
    os.utime(interface, (past, past))
    result = build(directory)
    assert '1 compiled, 1 up to date' in result.stderr, result.stderr
    result = build(directory)
    assert '0 compiled, 2 up to date; current' in result.stderr, result.stderr
    # A unit that loses its ownership declaration loses its record.
    (directory / 'src/box.cesta').write_text('(cesta native-c/1 compact-c/2\n  (unit\n    (include "box.h")\n'
        '    (native "const int *box_value(const Box *b) { return &b->value; }")))\n')
    build(directory)
    assert not (obj / 'box.cesta.o.contracts').exists() and not (directory / 'out/box/libbox.a.contracts').exists()

    shutil.rmtree(work)
    print(f'link: {programs} composed programs, {composed} cross-unit calls matched to checked definitions, '
          f'{len(cases)} contract refusals at the call, target refusals, native and unchecked obligations listed, a differing unchecked definition reported, '
          f'interfaces written from their definers, archive records, GCC/Clang records identical, interface freshness', flush=True)


if __name__ == '__main__':
    main()
