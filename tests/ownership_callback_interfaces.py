"""Callable contracts inferred from ordinary C record fields.

The interface is independent of implementation names. Installing a function
requires the real C prototype and a compatible resource signature; raw
function-pointer type compatibility alone does not admit an implementation.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from ownership_core import ROOT, BINARY
from oracles.pro_compact_cesta import parse
from ownership_composition import dump, section


HEADER = '''#include <stdint.h>
typedef struct Reader {
    const uint32_t *context;
    const uint32_t *(*read)(const uint32_t *context);
} Reader;
const uint32_t *current(const uint32_t *context);
'''
CONTRACTS = '''(callback ReadInterface (field Reader read) (returns-borrow 0 nullable))
(record Reader (field context (borrow read)) (field read (callback ReadInterface)))
(function current (returns-borrow 0))'''
PROVIDER = 'const uint32_t *current(const uint32_t *context) { return context; }\n'
CONSUMER = '''uint32_t run(Reader row) {
    const uint32_t *p = row.read(row.context);
    if (!p) return 0;
    return *p;
}
int main(void) { const uint32_t value = 7; Reader row = {&value,current}; return run(row) != 7; }
'''


def unit(body, contracts=''):
    return ('(cesta native-c/1 compact-c/2 (unit (ownership (check-all) ' + contracts +
            ') (include "reader.h") (native ' + json.dumps(body) + ')))\n')


def run(command, accepted=True, cwd=ROOT):
    result = subprocess.run(list(map(str, command)), cwd=cwd, text=True, capture_output=True)
    assert (result.returncode == 0) == accepted, (command, result.returncode, result.stdout, result.stderr)
    return result


def closed_boundaries(cesta, work, cc, cases=(
        'different_targets', 'open_into_closed', 'internal_scope', 'target_order', 'factory_targets')):
    fixtures = []
    for case in cases:
        directory = work / (case + '-' + cc)
        directory.mkdir()
        header = 'typedef const unsigned *(*Read)(const unsigned *);\n'
        header += 'typedef struct CallableShape {Read read;} CallableShape;\n'
        header += 'unsigned apply(Read next,const unsigned *p);\n'
        if case == 'factory_targets':
            header += 'typedef struct Reader {const unsigned *context; Read read;} Reader;\nReader produce(const unsigned *);\n'
        if case != 'internal_scope':
            header += 'const unsigned *first(const unsigned *);\nconst unsigned *second(const unsigned *);\n'
        (directory / 'reader.h').write_text(header)
        provider_targets = 'first second' if case == 'target_order' else 'private_read' if case == 'internal_scope' else 'first'
        caller_targets = 'second first' if case == 'target_order' else 'private_read' if case == 'internal_scope' else 'second'
        provider = ('static const unsigned *private_read(const unsigned*p){return p;}' if case == 'internal_scope' else
                    'const unsigned *first(const unsigned*p){return p;}')
        provider_contracts = (f'(callback Read (targets {provider_targets})) '
                              f'(function {provider_targets.split()[0]} (returns-borrow 0)) '
                              + ('' if case == 'factory_targets' else '(function apply (calls 0 Read))'))
        if case == 'target_order':
            provider += 'const unsigned *second(const unsigned*p){return p;}'
            provider_contracts += ' (function second (returns-borrow 0))'
        record_contract = '(record Reader (field context (borrow read)) (field read (callback Read)))'
        if case == 'factory_targets':
            provider += 'Reader produce(const unsigned*p){return (Reader){p,first};}'
            provider_contracts += record_contract + ' (function produce (returns-record ((context) (borrow 0)) ((read) static)))'
        else:
            provider += 'unsigned apply(Read next,const unsigned*p){return *next(p);}'
        caller = ('static const unsigned *private_read(const unsigned*p){return p;}' if case == 'internal_scope' else
                  '' if case == 'target_order' else 'const unsigned *second(const unsigned*p){return p;}')
        caller_contracts = ('(callback Read (field CallableShape read) (returns-borrow 0))' if case == 'open_into_closed' else
                            f'(callback Read (targets {caller_targets}))')
        if case == 'target_order':
            caller_contracts += (' (foreign first (returns-borrow 0) (obligation "Borrows its input."))'
                                 ' (foreign second (returns-borrow 0) (obligation "Borrows its input."))')
        else:
            caller_contracts += f' (function {caller_targets} (returns-borrow 0))'
        if case == 'factory_targets':
            caller_contracts += record_contract + ''' (foreign produce
                (returns-record ((context) (borrow 0)) ((read) static))
                (obligation "Returns a view with this callable contract."))'''
        else:
            caller_contracts += ' (foreign apply (calls 0 Read) (obligation "The provider meets this callable interface."))'
        selected = 'private_read' if case == 'internal_scope' else 'second'
        caller += ('int main(void){const unsigned n=7;Reader row=produce(&n);return *row.read(row.context)!=7;}'
                   if case == 'factory_targets' else
                   f'int main(void){{const unsigned n=7;return apply({selected},&n)!=7;}}')
        records, plans = [], []
        for stem, body, contracts in (('provider', provider, provider_contracts), ('consumer', caller, caller_contracts)):
            source = directory / (stem + '.cesta')
            source.write_text(unit(body, contracts))
            plan, record = directory / (stem + '.plan'), directory / (stem + '.contracts')
            run([cesta, 'export-c', '--cc', cc, '--plan', plan, '-o', directory / (stem + '.c'),
                 source, '--', '-std=c11', '-I' + str(directory)])
            run([cesta, 'cc', '--cc', cc, '--contracts', record, '-std=c11', '-I' + str(directory),
                 '-c', source, '-o', directory / (stem + '.o')])
            records.append(record)
            plans.append(plan)
        accepted = case == 'target_order'
        linked = run([cesta, 'link-check', *records], accepted=accepted)
        if accepted:
            binary = directory / 'app'
            run([cc, directory / 'provider.o', directory / 'consumer.o', '-o', binary])
            run([binary])
            legacy = []
            for record in records:
                document, = parse(record.read_bytes())
                for entry in document[2][4:]:
                    if entry[0] == 'define' and entry[3] != 'external':
                        continue
                    for group in section(section(entry, 'contract'), 'callbacks')[1:]:
                        del group[2]
                old = record.with_name('legacy-' + record.name)
                old.write_text(dump(document) + '\n')
                legacy.append(old)
            run([cesta, 'link-check', *legacy], accepted=False)
        else:
            assert 'callback contract' in linked.stderr
        if case == 'internal_scope':
            # Even equal source identities do not merge separate unit scopes.
            for record in records:
                document, = parse(record.read_bytes())
                old = document[2][1]
                # Use Text atoms so only the source identity changes.
                from oracles.pro_compact_cesta import Text
                def identity(node):
                    if isinstance(node, list):
                        return [identity(child) for child in node]
                    return Text(b'same-source.cesta') if node == old else node
                record.write_text(dump(identity(document)) + '\n')
            assert 'internal target crosses units' in run([cesta, 'link-check', *records], accepted=False).stderr
        fixtures.append((records, plans, directory, accepted))
    return fixtures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cesta', type=Path, default=BINARY)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--admit', action='store_true')
    parser.add_argument('--lean-root', type=Path, default=ROOT.parent / 'Mettapedia/lean/mettapedia')
    args = parser.parse_args()
    cesta = args.cesta.resolve()
    work = args.work.resolve() if args.work else Path(tempfile.mkdtemp(prefix='callback-interfaces-', dir=ROOT / 'build'))
    work.mkdir(parents=True, exist_ok=True)
    plans = []
    compositions = []
    boundary_compositions = []
    additional_plans = []
    executions = 0
    for cc in ('gcc', 'clang'):
        directory = work / cc
        directory.mkdir()
        (directory / 'reader.h').write_text(HEADER)
        (directory / 'provider.cesta').write_text(unit(PROVIDER, CONTRACTS))
        interface = directory / 'reader.h.ownership'
        command = [cesta, 'export-interface', '--cc', cc, '-o', interface, directory / 'reader.h',
                   directory / 'provider.cesta', '--', '-std=c11', '-I' + str(directory)]
        run(command)
        first = interface.read_bytes()
        assert b'(callback ReadInterface (field Reader read)' in first
        assert b'@callback/' not in first, 'an internal interface name leaked into the C header contract'
        run(command)
        assert interface.read_bytes() == first
        (directory / 'consumer.cesta').write_text(unit(CONSUMER))
        records = []
        for stem in ('provider', 'consumer'):
            source = directory / (stem + '.cesta')
            plan, record = directory / (stem + '.plan'), directory / (stem + '.contracts')
            run([cesta, 'export-c', '--cc', cc, '--plan', plan, '-o', directory / (stem + '.c'),
                 source, '--', '-std=c11', '-I' + str(directory)])
            document, = parse(plan.read_bytes())
            assert document[1].data == b'cesta-ownership/7'
            assert any(item[0] == 'callback-interfaces' for item in document[2:])
            plans.append(plan)
            run([cesta, 'cc', '--cc', cc, '--contracts', record, '-std=c11', '-O2', '-I' + str(directory),
                 '-c', source, '-o', directory / (stem + '.o')])
            records.append(record)
        linked = run([cesta, 'link-check', '--obligations', directory / 'app.obligations', *records])
        assert '1 cross-unit calls meet checked definitions' in linked.stderr
        obligations = (directory / 'app.obligations').read_text()
        assert '@callback/ReadInterface' in obligations and 'entry-supplied callback' in obligations
        consumer_plan = plans[-1].read_text()
        assert '(ReadInterface current)' in consumer_plan and 'callback-implementations' in consumer_plan
        consumer_document, = parse(plans[-1].read_bytes())
        bindings = next(item[1:] for item in consumer_document[2:] if item[0] == 'callback-implementations')
        assert all(binding[1] != '@callback/ReadInterface' for binding in bindings)
        compositions.append((records, plans[-2:], directory))
        for sanitized in (False, True):
            binary = directory / f'app-{sanitized}'
            run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
                 *(['-fsanitize=address,undefined', '-fno-pie', '-no-pie'] if sanitized else []),
                 directory / 'provider.c', directory / 'consumer.c', '-o', binary])
            run([binary])
            executions += 1

        # Pass the reference-bearing record across the actual unit boundary,
        # so the linker must compare its callback contract and real C layout.
        directory = work / ('record-' + cc)
        directory.mkdir()
        (directory / 'reader.h').write_text(HEADER + 'uint32_t invoke_reader(Reader row);\n')
        (directory / 'reader.h.ownership').write_text(
            '(cesta ownership-interface/1 ' + CONTRACTS + ' (function invoke_reader))\n')
        (directory / 'provider.cesta').write_text(unit(PROVIDER + '''
          uint32_t invoke_reader(Reader row) {
            const uint32_t *p=row.read(row.context); if(!p) return 0; return *p;
          }'''))
        (directory / 'consumer.cesta').write_text(unit('''
          int main(void) { const uint32_t x=7; Reader row={&x,current};
            return invoke_reader(row)!=7; }'''))
        records = []
        emitted = []
        for stem in ('provider', 'consumer'):
            source = directory / (stem + '.cesta')
            plan, record = directory / (stem + '.plan'), directory / (stem + '.contracts')
            run([cesta, 'export-c', '--cc', cc, '--plan', plan, '-o', directory / (stem + '.c'),
                 source, '--', '-std=c11', '-I' + str(directory)])
            run([cesta, 'cc', '--cc', cc, '--contracts', record, '-std=c11', '-O2',
                 '-I' + str(directory), '-c', source, '-o', directory / (stem + '.o')])
            records.append(record)
            emitted.append(plan)
        linked = run([cesta, 'link-check', *records])
        assert '2 cross-unit calls meet checked definitions' in linked.stderr
        compositions.append((records, emitted, directory))
        plans.extend(emitted)
        binary = directory / 'app'
        run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
             directory / 'provider.c', directory / 'consumer.c', '-o', binary])
        run([binary])
        executions += 1
        for mutation in ('callback', 'layout'):
            document, = parse(records[0].read_bytes())
            definition = next(entry for entry in document[2][4:]
                              if entry[0] == 'define' and entry[1] == 'invoke_reader')
            contract = section(definition, 'contract')
            if mutation == 'callback':
                group = section(contract, 'callbacks')[1]
                section(group, 'signature')[3][-1] = 'nonnull'
            else:
                section(contract, 'records')[1][2] = '24'
            changed = directory / (mutation + '.contracts')
            changed.write_text(dump(document) + '\n')
            refused = run([cesta, 'link-check', changed, records[1]], accepted=False)
            assert ('callback contract' if mutation == 'callback' else 'record layout') in refused.stderr

    negative = {
        'wrong_origin': (CONTRACTS.replace('(function current (returns-borrow 0))',
                                          '(function current (returns-static))'),
                         'static const uint32_t value = 7;\n'
                         'const uint32_t *current(const uint32_t *p) { if (p) return &value; return &value; }\n' + CONSUMER),
        'nullable_result_into_nonnull': (CONTRACTS.replace('(returns-borrow 0 nullable)', '(returns-borrow 0)')
                                         .replace('(function current (returns-borrow 0))',
                                                  '(function current (returns-borrow 0 nullable))'),
                                         'const uint32_t *current(const uint32_t *p) { if (!*p) return 0; return p; }\n' + CONSUMER),
        'stronger_argument_requirement': (CONTRACTS.replace('(field Reader read)', '(field Reader read) (nullable 0)'),
                                           PROVIDER + CONSUMER),
        'raw_pointer_provenance': (CONTRACTS,
                                  PROVIDER + 'typedef const uint32_t *(*Raw)(const uint32_t *);\n'
                                  'void set(Reader *row, Raw callback) { row->read = callback; }\n'),
        'unknown_member': (CONTRACTS.replace('(field Reader read)', '(field Reader missing)'), PROVIDER),
        'missing_result_lifetime': (CONTRACTS.replace(' (returns-borrow 0 nullable)', ''), PROVIDER),
        'open_into_closed_whitelist': (CONTRACTS + '''
            (callback Closed (targets current)) (function accept (calls 0 Closed))''',
            PROVIDER + '''typedef const uint32_t *(*Closed)(const uint32_t *);
            uint32_t accept(Closed next, const uint32_t *p) { return *next(p); }
            uint32_t relay(Reader row) { return accept(row.read, row.context); }'''),
        'unchecked_implementation': (CONTRACTS + '''
            (unchecked (unproved) (obligation "This implementation is not checked."))''',
            PROVIDER + '''const uint32_t *unproved(const uint32_t *p) { return p; }
            uint32_t use(const uint32_t *p) { Reader row={p,unproved}; return *row.read(row.context); }'''),
        'stack_context_escape': (CONTRACTS + '''
            (function escape (returns-record ((context) static) ((read) static)))''',
            PROVIDER + 'Reader escape(void) { const uint32_t x=7; return (Reader){&x,current}; }'),
        'context_mutated_while_borrowed': (CONTRACTS,
            PROVIDER + '''uint32_t wrong(void) { uint32_t x=7; Reader row={&x,current};
              x=9; const uint32_t *p=row.read(row.context); if(!p) return 0; return *p; }'''),
        'nullable_field_unguarded': (CONTRACTS.replace('(field read (callback ReadInterface))',
                                                       '(field read (callback ReadInterface nullable))'),
                                    PROVIDER + CONSUMER),
    }
    for name, (contracts, body) in negative.items():
        directory = work / name
        directory.mkdir()
        (directory / 'reader.h').write_text(HEADER)
        source = directory / 'unit.cesta'
        source.write_text(unit(body, contracts))
        for cc in ('gcc', 'clang'):
            result = run([cesta, 'cc', '--cc', cc, '-std=c11', '-I' + str(directory),
                          '-fsyntax-only', source], accepted=False)
            assert 'ownership' in result.stderr

    for cc in ('gcc', 'clang'):
        directory = work / ('retained-' + cc)
        directory.mkdir()
        (directory / 'reader.h').write_text(HEADER)
        source = directory / 'unit.cesta'
        source.write_text(unit('''
          typedef struct Saver { void (*save)(const uint32_t *, const uint32_t **); } Saver;
          void save(const uint32_t *p,const uint32_t **out) { *out=p; }
          void relay(Saver saver,const uint32_t *p,const uint32_t **out) { saver.save(p,out); }
          int main(void) { const uint32_t x=7; const uint32_t *out=0; Saver saver={save};
            relay(saver,&x,&out); return *out!=7; }''', '''
          (callback Save (field Saver save) (stores 1 0))
          (record Saver (field save (callback Save)))
          (function save (stores 1 0)) (function relay (stores 2 1))'''))
        plan, exported, binary = directory / 'unit.plan', directory / 'unit.c', directory / 'app'
        run([cesta, 'export-c', '--cc', cc, '--plan', plan, '-o', exported,
             source, '--', '-std=c11', '-I' + str(directory)])
        additional_plans.append(plan)
        run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
             exported, '-o', binary])
        run([binary])
        executions += 1

        directory = work / ('nullable-' + cc)
        directory.mkdir()
        (directory / 'reader.h').write_text(HEADER)
        source = directory / 'unit.cesta'
        contracts = CONTRACTS.replace('(field read (callback ReadInterface))',
                                      '(field read (callback ReadInterface nullable))')
        body = PROVIDER + '''uint32_t optional(Reader row) {
          if (!row.read) return 0;
          const uint32_t *p=row.read(row.context); if (!p) return 0; return *p;
        }
        int main(void) { const uint32_t x=7; Reader a={&x,current}, b={&x,0};
          return optional(a)+optional(b)!=7; }
        '''
        source.write_text(unit(body, contracts))
        plan = directory / 'unit.plan'
        exported = directory / 'unit.c'
        run([cesta, 'export-c', '--cc', cc, '--plan', plan, '-o', exported,
             source, '--', '-std=c11', '-I' + str(directory)])
        additional_plans.append(plan)
        binary = directory / 'app'
        run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
             exported, '-o', binary])
        run([binary])
        executions += 1

        # An indirect closed-interface call may reach every listed target.
        # They must all be visible at the program's link boundary.
        directory = work / ('closed-link-' + cc)
        directory.mkdir()
        (directory / 'reader.h').write_text(HEADER)
        contracts = '''(callback Closed (targets current other))
          (foreign current (returns-borrow 0) (obligation "Borrows its input."))
          (foreign other (returns-borrow 0) (obligation "Borrows its input."))
          (record Reader (field context (borrow read)) (field read (callback Closed)))'''
        source = directory / 'unit.cesta'
        source.write_text(unit('''typedef const uint32_t *(*Closed)(const uint32_t *);
          const uint32_t *other(const uint32_t *p);
          uint32_t read_closed(Reader row) { return *row.read(row.context); }''', contracts))
        record = directory / 'unit.contracts'
        run([cesta, 'cc', '--cc', cc, '--contracts', record, '-std=c11', '-I' + str(directory),
             '-fsyntax-only', source])
        document, = parse(record.read_bytes())
        assert {str(entry[1]) for entry in document[2][4:] if entry[0] == 'assume'} == {'current', 'other'}
        boundary_compositions.extend(closed_boundaries(cesta, work, cc))

    if args.admit:
        from ownership_composition import qualify
        from ownership_admission import admit_record
        for records, emitted, directory in compositions:
            qualify(records, emitted, directory / 'composition', args.lean_root.resolve())
        for plan in additional_plans:
            record, = parse(plan.read_bytes())
            admit_record(record, plan.with_suffix('.lean'), args.lean_root.resolve())
        for records, emitted, directory, accepted in boundary_compositions:
            qualify(records, emitted, directory / 'composition', args.lean_root.resolve(), accepted=accepted)
    print(f'callback interfaces: {executions} GCC/Clang/sanitizer executions, {len(plans)+len(additional_plans)} actual plans, '
          f'{len(negative)} source refusals; inferred C prototypes, shared export fixpoint, '
          'nonnull result refinement, explicit dynamic obligations' + ('; kernel-admitted plans' if args.admit else ''), flush=True)
    print(f'callback boundaries: {2*len(boundary_compositions)} additional actual plans; '
          'reordered external target sets accepted; different sets, '
          'open-to-closed and distinct internal scopes refused in both compilers' +
          ('; actual body plans and positive/negative compositions kernel-checked' if args.admit else ''), flush=True)
    if not args.work:
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
