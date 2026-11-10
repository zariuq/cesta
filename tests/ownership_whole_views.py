"""Whole native spans, nested retained views and independent plan admission."""

import argparse
import json
from pathlib import Path
import tempfile

from checked_spans import BINARY, ELEMENTS, ROOT, run


def document(code, contracts):
    return '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) ' + contracts + \
        ') (native ' + json.dumps(code) + ')))'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--admit', action='store_true')
    parser.add_argument('--lean-root', type=Path, default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='whole-views-', dir=ROOT/'build'))
    plans = []
    executions = 0
    for element, (ctype, _, _) in ELEMENTS.items():
        directory = work/element
        directory.mkdir()
        checked = directory/'access.cesta'
        checked.write_text(f'''(cesta checked-c/1 (module whole_access) (word-bits 64)
          (failure access_failure)
          (fn get (params (bytes (span {element} read)) (index usize))
            (returns {element}) (effects) (body (return (at bytes index))))
          (fn set (params (bytes (span {element} write)) (index usize) (value {element}))
            (returns unit) (effects) (body (put bytes index value) (return))))''')
        run([BINARY, 'checked', 'header', checked, '-o', directory/'access.h'])
        run([BINARY, 'export-c', checked, '-o', directory/'access.c'])
        read = 'cesta_read_bytes' if element == 'u8' else f'cesta_read_{element}_span'
        write = 'cesta_write_bytes' if element == 'u8' else f'cesta_write_{element}_span'
        (directory/'api.h').write_text(f'''#include "access.h"
typedef {read} Read;
typedef {write} Write;
typedef struct {{Read bytes;}} Payload;
typedef struct {{Payload firing;}} Event;
Read identity(Read input);
Write writer(Write input);
Event capture(Read input);
Read event_view(const Event *event);
size_t count(const Event *event);
{ctype} event_at(const Event *event,size_t index);
void store(Read input,Event *out);
int checked_client(void);
int checked_write_client(void);
''')
        contracts = f'''(view Read data len read {element}) (view Write data len write {element})
          (record Payload) (record Event)
          (function identity (returns-borrow 0)) (function writer (returns-borrow 0))
          (function capture (returns-record ((firing bytes) (borrow 0))))
          (function event_view (returns-borrow 0 firing bytes))
          (function store (stores-record 1 ((firing bytes) (borrow 0))))
          (foreign whole_access_get (obligation "Checked span read; retains no reference."))
          (foreign whole_access_set (obligation "Checked span write; retains no reference."))'''
        code = f'''#include "api.h"
Read identity(Read input){{return input;}}
Write writer(Write input){{return input;}}
Event capture(Read input){{Event e={{.firing={{.bytes=input}}}};return e;}}
Read event_view(const Event *e){{return e->firing.bytes;}}
size_t count(const Event *e){{Read local=e->firing.bytes;return local.len;}}
{ctype} event_at(const Event *e,size_t index){{
  if(index>=e->firing.bytes.len)return 0;
  return whole_access_get(e->firing.bytes,index);
}}
void store(Read input,Event *out){{out->firing.bytes=input;}}
int checked_client(void){{
  {ctype} data[4]={{1,0,1,0}};Event e=capture((Read){{data,4}});
  {{Read temporary=identity((Read){{data,4}});Event copy=capture(temporary);e=copy;}}
  Read got=event_view(&e);store(got,&e);
  return count(&e)==4 && event_at(&e,0)==1 ? 0 : 1;
}}
int checked_write_client(void){{
  {ctype} data[4]={{0}};
  {{Write source={{data,4}};{{Write child=writer(source);whole_access_set(child,0,1);}}}}
  return data[0]==1 ? 0 : 1;
}}
'''
        source = directory/'views.cesta'
        source.write_text(document(code, contracts))
        (directory/'host.c').write_text(f'''#include "api.h"
#include <assert.h>
#include <stdlib.h>
void access_failure(void){{abort();}}
int main(void){{
  {ctype} data[16];for(size_t i=0;i<16;++i)data[i]=({ctype})(i%2);
  for(size_t n=0;n<=16;++n){{
    Event e=capture((Read){{data,n}});
    assert(count(&e)==n);assert(event_view(&e).data==data);
    for(size_t i=0;i<n;++i)assert(event_at(&e,i)==data[i]);
  }}
  Event empty=capture((Read){{NULL,0}});assert(count(&empty)==0);
  assert(!checked_client());assert(!checked_write_client());return 0;
}}
''')
        for cc in ('gcc', 'clang'):
            (directory/cc).mkdir()
            exported, plan = directory/cc/'views.c', directory/f'{cc}.plan'
            run([BINARY, 'export-c', source, '--std=c11', '--cc', cc,
                 '-o', exported, '--plan', plan, '--', '-I'+str(directory)])
            for sanitize in (False, True):
                flags = ['-std=c11', '-O2', '-g0', '-Wall', '-Wextra', '-Werror', '-I'+str(directory)]
                if sanitize:
                    flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
                binary = directory/f'{cc}-{sanitize}'
                run([cc, *flags, exported, directory/'access.c', directory/'host.c', '-o', binary])
                run([binary]); executions += 1
                if not sanitize:
                    authored = directory/f'{cc}-authored'
                    run([BINARY, 'cc', '--cc', cc, *flags, source, directory/'access.c',
                         directory/'host.c', '-o', authored])
                    run([authored]); executions += 1
                    assert authored.read_bytes() == binary.read_bytes(), (element, cc, 'export identity')
        plans.append(directory/'gcc.plan')
        if element == 'u32':
            run([BINARY, 'export-c', source, '--std=c11', '--cc', 'clang',
                 '-o', directory/'arm64.c', '--plan', directory/'arm64.plan',
                 '--', '--target=aarch64-none-elf', '-I'+str(directory)])
            run(['clang', '--target=aarch64-none-elf', '-std=c11', '-Wall', '-Wextra', '-Werror',
                 '-I'+str(directory), '-c', directory/'arm64.c', '-o', directory/'arm64.o'])
            assert b'aapcs64' in (directory/'arm64.plan').read_bytes()
            negative_home, negative_contracts, negative_code = directory, contracts, code

    negatives = {
        'raw_index': ('uint32_t bad(Read r){return r.data[0];}', ''),
        'raw_deref': ('uint32_t bad(Read r){return *r.data;}', ''),
        'fixed_pointer_call': ('extern uint32_t raw(const uint32_t *p);uint32_t bad(Read r){return raw(r.data);}',
                              '(foreign raw (obligation "Reads one live uint32_t."))'),
        'fixed_field_call': ('typedef struct {const uint32_t *p;} Raw;extern uint32_t raw_record(const Raw *p);'
                             'uint32_t bad(Read r){Raw q={r.data};return raw_record(&q);}',
                             '(record Raw (field p (borrow read)))'
                             '(foreign raw_record (obligation "Reads a complete field pointee."))'),
        'length_mismatch': ('Read bad(Read a,Read b){return (Read){a.data,b.len};}', '(function bad (returns-borrow 0))'),
        'shortened_dynamic': ('Read bad(Read r){return (Read){r.data,1};}', '(function bad (returns-borrow 0))'),
        'wrong_input': ('Event bad(Read a,Read b){return capture(b);}',
                        '(function bad (returns-record ((firing bytes) (borrow 0))))'),
        'stack_escape': ('Event bad(Read r){uint32_t local[4]={0};return capture((Read){local,4});}',
                         '(function bad (returns-record ((firing bytes) (borrow 0))))'),
        'caller_dangling': ('size_t bad(Read r){Event e=capture(r);{uint32_t local[4]={0};e=capture((Read){local,4});}return count(&e);}', ''),
        'length_write': ('void bad(Read r){r.len=0;}', ''),
        'length_address': ('void bad(Read r){size_t *p=&r.len;*p=0;}', ''),
        'uninitialized_metadata': ('size_t bad(void){Read r;return r.len;}', ''),
        'volatile_pointer': ('typedef struct {const uint32_t *volatile data;size_t len;} Volatile;'
                             'size_t bad(Volatile r){return r.len;}', '(view Volatile data len read u32)'),
        'volatile_length': ('typedef struct {const uint32_t *data;volatile size_t len;} Volatile;'
                            'size_t bad(Volatile r){return r.len;}', '(view Volatile data len read u32)'),
        'atomic_length': ('typedef struct {const uint32_t *data;_Atomic(size_t) len;} Atomic;'
                          'size_t bad(Atomic r){return r.len;}', '(view Atomic data len read u32)'),
        'exclusive_copy': ('void bad(Write r){Write a=r;Write b=r;whole_access_set(a,0,1);whole_access_set(b,0,1);}', ''),
        'read_write_overlap': ('void bad(Write r){Read shared={r.data,r.len};whole_access_set(r,0,1);(void)whole_access_get(shared,0);}', ''),
        'partial_result': ('Event bad(Read r){Event e;return e;}',
                           '(function bad (returns-record ((firing bytes) (borrow 0))))'),
        'contract_rights': ('extern Write bad(Read r);',
                            '(foreign bad (returns-borrow 0) (obligation "Untrue exclusive result."))'),
    }
    for name, (body, extra) in negatives.items():
        source = negative_home/f'bad-{name}.cesta'
        source.write_text(document(negative_code+'\n'+body, negative_contracts+extra))
        refused = run([BINARY, 'export-c', source, '--std=c11', '--', '-I'+str(negative_home)], accepted=False)
        assert b'ownership' in refused.stderr, (name, refused.stderr.decode())

    if args.admit:
        import copy
        import ownership_admission as admission
        for plan in plans:
            record, = admission.parse(plan.read_bytes())
            admission.admit_record(record, plan.parent/'WholeViews.lean', args.lean_root)
            if plan.parent.name == 'u32':
                for label in ('old-format', 'concrete-root', 'wrong-whole-root'):
                    changed = copy.deepcopy(record)
                    if label == 'old-format':
                        changed[1] = admission.Text(b'cesta-ownership/3')
                    else:
                        fn = next(item for item in changed[2:] if item[0] == 'function' and item[1] == 'identity')
                        event = next(e for e in admission.walk(fn[6][1:]) if e[0] == 'whole-root')
                        if label == 'concrete-root':
                            event[:] = [admission.Sym('root'), event[1], admission.Sym('1'),
                                        event[2], admission.Sym('input'), admission.Sym('0')]
                        else:
                            event[1] = admission.Sym('999')
                    try:
                        admission.module_parts(changed)
                    except AssertionError:
                        pass
                    else:
                        raise AssertionError(('extent-unit mutation accepted', label))
    print(f'whole native views: six scalar elements, {executions} GCC/Clang executions, '
          f'{len(negatives)} source refusals, export identity, AAPCS64 and zero lengths'
          + ('; actual plans and arbitrary-extent prefixes kernel-checked' if args.admit else ''))
    print('Evidence:', work)


if __name__ == '__main__':
    main()
