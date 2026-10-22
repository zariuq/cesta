"""Native checked-contract and diagnostic counter controls."""

import copy
import json
from pathlib import Path

from oracles.pro_compact_cesta import Sym
from protocols import CESTA, ROOT, declaration, module, run, semantics

FLAGS = ['-std=c11', '-Wall', '-Wextra', '-Wpedantic', '-Werror']


def controls(work):
    name, data = declaration(ROOT / 'protocols/Admission.module')
    header = work / 'instrumented.h'
    run([CESTA, 'protocol', 'export', ROOT / 'protocols/Admission.module', 'Protocols', '-o', header])
    instrumented_header = header.read_bytes()
    # The independent trace fixture is extended with an actual owner and facts.
    from protocols import harness
    text = harness(name, data, header)
    text = text.replace('size_t len; }', 'size_t len; bool exact, ready, owned; unsigned fault; struct admission_run_counters *counts; }')
    text = text.replace('  unsigned reply =', '  if (s->fault) fprintf(stderr, "CALL %s\\n", name);\n  unsigned reply =')
    text = text.replace('return event(s, "admission_arity", true);',
                        's->exact = event(s, "admission_arity", true); return s->exact;')
    text = text.replace('return event(s, "admission_prepare", true);',
                        's->ready = event(s, "admission_prepare", true); return s->ready;')
    text = text.replace('return event(s, "admission_acquire", true);',
                        'bool result = event(s, "admission_acquire", true); s->owned = s->fault == 2 ? true : result; return result;')
    text = text.replace('(void)event(s, "admission_release", false);',
                        '(void)event(s, "admission_release", false); if (s->fault != 3) s->owned = false;')
    validators = '''
static inline bool admission_has_exact_arity(AdmissionContext *s) { return s->exact && s->fault != 4; }
static inline bool admission_is_ready(AdmissionContext *s) { return s->ready; }
static inline bool admission_owns_arguments(AdmissionContext *s) { return s->owned; }
#define admission_run_COUNT_DECLINE(ctx, reason) admission_run_count_decline((ctx)->counts, reason)
'''
    text = text.replace('#include "instrumented.h"', validators + '#include "instrumented.h"')
    text = text.replace('if (argc != 2)', 'if (argc != 4)')
    text = text.replace('  unsigned result = admission_run(&s);', '''
  admission_run_counters counts = {0}; s.counts = &counts;
  s.fault = (unsigned)strtoul(argv[2], NULL, 10); s.owned = s.fault == 1;
  if (strtoul(argv[3], NULL, 10)) for (unsigned i = 0; i < admission_run_decline_count; ++i) counts.declines[i] = UINT64_MAX;
  unsigned result = admission_run(&s);''')
    text = text.replace('  return 0;', '''
  for (unsigned i = 0; i < admission_run_decline_count; ++i)
    printf("%llu%c", (unsigned long long)counts.declines[i], i + 1 == admission_run_decline_count ? '\\n' : ' ');
  return 0;''')
    probe = work / 'instrumentation.c'; probe.write_text(text)
    executions, refusals = 0, 0
    for compiler in ('gcc', 'clang'):
        for opt in ('-O0', '-O2'):
            exe = work / (compiler + opt + '-checked')
            run([compiler, *FLAGS, opt, '-DNDEBUG', '-Dadmission_run_CHECKED=1', probe, '-o', exe])
            for bits in range(16):
                output = run([exe, bits, 0, 0]).stdout.splitlines()
                assert output[0] == semantics(data, bits)
                # Exported reasons follow true-branch then false-branch traversal:
                # prepare-false (after cleanup), arity-false.
                expected = [int(bits & 7 == 3), int(not bits & 1)]
                assert list(map(int, output[1].split())) == expected, (bits, output)
                saturated = run([exe, bits, 0, 1]).stdout.splitlines()
                assert saturated[0] == output[0]
                assert list(map(int, saturated[1].split())) == [2**64-1, 2**64-1]
                executions += 2
            for bits, fault, message in ((0, 1, 'own arguments must be false'),
                                        (1, 2, 'own arguments must be false'),
                                        (15, 3, 'own arguments must be false'),
                                        (1, 4, 'fact exact_arity must be true')):
                result = run([exe, bits, fault, 0], ok=False)
                assert message in result.stderr, result.stderr
                expected_calls = {1: [], 2: ['admission_arity', 'admission_acquire'],
                                  3: ['admission_arity', 'admission_acquire', 'admission_prepare',
                                      'admission_execute', 'admission_release'], 4: ['admission_arity']}[fault]
                assert [line[5:] for line in result.stderr.splitlines() if line.startswith('CALL ')] == expected_calls
                refusals += 1
    # Independent ordinary-C baseline: disabled checks/counters add no object
    # bytes, at either optimization level. Compile the same path each time.
    plain = work / 'plain.c'; plain.write_text(harness(name, data, header))
    reference = '''
typedef enum { admission_run_declined, admission_run_answered, admission_run_done,
 admission_run_raised, admission_run_suspended, admission_run_resource_failure,
 admission_run_engine_failure } admission_run_outcome;
static inline admission_run_outcome admission_run(AdmissionContext *ctx) {
 if (!admission_arity(ctx)) return admission_run_declined;
 if (!admission_acquire(ctx)) return admission_run_resource_failure;
 if (!admission_prepare(ctx)) { admission_release(ctx); return admission_run_declined; }
 if (admission_execute(ctx)) { admission_release(ctx); return admission_run_answered; }
 admission_release(ctx); return admission_run_raised;
}
'''
    for compiler in ('gcc', 'clang'):
        for opt in ('-O0', '-O2'):
            header.write_bytes(instrumented_header)
            current = work / (compiler + opt + '-instrumented.o')
            run([compiler, *FLAGS, opt, '-c', plain, '-o', current])
            header.write_text(reference)
            baseline = work / (compiler + opt + '-ordinary.o')
            run([compiler, *FLAGS, opt, '-c', plain, '-o', baseline])
            assert current.read_bytes() == baseline.read_bytes(), (compiler, opt, 'disabled instrumentation changed objects')
    header.write_bytes(instrumented_header)
    # True replies and acquisition failures have their own reasons; cleanup
    # must not replace the most recent Boolean decision.
    variants = [
        ('true', [Sym('Call'), Sym('arity'), [Sym('Return'), Sym('declined')], [Sym('Return'), Sym('done')]],
         [(0, 0), (1, 1)]),
        ('acquire', [Sym('Call'), Sym('arity'),
         [Sym('Acquire'), Sym('arguments'), [Sym('Release'), Sym('arguments'), [Sym('Return'), Sym('done')]],
          [Sym('Return'), Sym('declined')]], [Sym('Return'), Sym('done')]], [(0, 0), (1, 1), (3, 0)])]
    for label, body, cases in variants:
        value = copy.deepcopy(data); value[6][1] = body
        path = work / ('reason-' + label + '.module'); path.write_text(module(name, value))
        run([CESTA, 'protocol', 'export', path, 'Protocols', '-o', header])
        assert ('admission_run_declined_after_admission_' + ('arity_true' if label == 'true' else 'acquire_false')) in header.read_text()
        source = work / ('reason-' + label + '.c')
        references = ''.join('  (void)' + fn + ';\n' for fn in (
            'admission_arity', 'admission_prepare', 'admission_execute', 'admission_acquire', 'admission_release',
            'admission_has_exact_arity', 'admission_is_ready', 'admission_owns_arguments'))
        source.write_text(text.replace('  unsigned result = admission_run(&s);', references + '  unsigned result = admission_run(&s);'))
        for compiler in ('gcc', 'clang'):
            exe = work / (compiler + '-reason-' + label)
            run([compiler, *FLAGS, '-O2', '-Dadmission_run_CHECKED=1', source, '-o', exe])
            for bits, expected in cases:
                output = run([exe, bits, 0, 0]).stdout.splitlines()
                assert output[0] == semantics(value, bits)
                assert output[1] == str(expected), output
                executions += 1
    header.write_bytes(instrumented_header)
    # Legacy modules remain usable but may not advertise checked execution.
    legacy = work / 'legacy.module'; legacy.write_text(module(name, data[:7]))
    run([CESTA, 'protocol', 'export', legacy, 'Protocols', '-o', header])
    for compiler in ('gcc', 'clang'):
        result = run([compiler, *FLAGS, '-Dadmission_run_CHECKED=1', '-fsyntax-only', probe], ok=False)
        assert 'requires validators' in result.stderr
        refusals += 1
    # Reject invalid mappings and generated namespace collisions at admission.
    cases = []
    for label, edit in (
        ('duplicate', lambda x: x[7].append(copy.deepcopy(x[7][1]))),
        ('unknown', lambda x: x[7][1].__setitem__(1, Sym('absent'))),
        ('kind', lambda x: x[7][1].__setitem__(0, Sym('Guard'))),
        ('keyword', lambda x: x[7][1].__setitem__(2, Sym('while'))),
        ('native-collision', lambda x: x[7][1].__setitem__(2, x[3][1][2])),
        ('generated-collision', lambda x: x[7][1].__setitem__(2, Sym(name + '_counters'))),
        ('reason-collision', lambda x: x[2].__setitem__(1, Sym(name + '_declined_after_admission_arity_false'))),
        ('config-collision', lambda x: x[3][1].__setitem__(2, Sym(name + '_CHECKED')))):
        value = copy.deepcopy(data); edit(value); cases.append((label, value))
    for label, value in cases:
        path = work / (label + '.module'); path.write_text(module(name, value))
        run([CESTA, 'protocol', 'check', path, 'Protocols'], ok=False); refusals += 1
    for native in ('stderr', 'printf', 'abort', 'malloc', 'uint64_t', 'UINT64_MAX'):
        value = copy.deepcopy(data); value[7][1][2] = Sym(native)
        path = work / ('prelude-' + native + '.module'); path.write_text(module(name, value))
        result = run([CESTA, 'protocol', 'check', path, 'Protocols'], ok=False)
        assert 'standard-header declaration' in result.stderr
        refusals += 1
    # An unconditional decline has a named reason; no-decline protocols are legal C.
    for outcome in ('declined', 'done'):
        # Preserve strings versus symbols using the parser's carriers.
        value = copy.deepcopy(data[:7]); value[2] = [Sym('Context'), Sym('SimpleContext')]
        value[3] = [Sym('Hooks')]; value[4] = [Sym('Resources')]
        value[6] = [Sym('Body'), [Sym('Return'), Sym(outcome)]]
        path = work / ('simple-' + outcome + '.module'); path.write_text(module('simple', value))
        run([CESTA, 'protocol', 'export', path, 'Protocols', '-o', header])
        source = work / ('simple-' + outcome + '.c')
        source.write_text('struct simple_counters; typedef struct { struct simple_counters *counts; } SimpleContext;\n'
             '#define simple_COUNT_DECLINE(ctx, reason) simple_count_decline((ctx)->counts, reason)\n'
             '#define simple_CHECKED 1\n#include "instrumented.h"\n'
             'int main(void) { simple_counters counts = {0}; SimpleContext ctx = {&counts};\n'
             'if (simple(&ctx) != simple_' + outcome + ') return 1;\n' +
             ('return counts.declines[simple_declined_at_entry] != 1;\n' if outcome == 'declined' else 'return 0;\n') + '}\n')
        for compiler in ('gcc', 'clang'):
            exe = work / (compiler + '-simple-' + outcome)
            run([compiler, *FLAGS, '-O0', source, '-o', exe]); run([exe]); executions += 1
    (work / 'instrumentation-summary.json').write_text(json.dumps(dict(executions=executions, refusals=refusals), indent=2)+'\n')
    return executions, refusals
