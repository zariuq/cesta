"""Scalar spans: exact C ABI, allocation boundaries, lifetimes and refusals."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
ELEMENTS = {"bool": ("bool", "true", "false"), "u8": ("uint8_t", "(u8 251)", "(u8 0)"),
            "u16": ("uint16_t", "(u16 65531)", "(u16 0)"),
            "u32": ("uint32_t", "(u32 4294967291)", "(u32 0)"),
            "u64": ("uint64_t", "(u64 18446744073709551611)", "(u64 0)"),
            "usize": ("size_t", "(usize 131071)", "(usize 0)")}


def run(args, *, accepted=True):
    p = subprocess.run(list(map(str, args)), cwd=ROOT, capture_output=True,
                       env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1:abort_on_error=1"})
    if (p.returncode == 0) != accepted:
        raise AssertionError((args, p.returncode, p.stdout.decode(), p.stderr.decode()))
    return p


def document(functions, name="spans", imports=""):
    return f'''(cesta checked-c/1 (module {name}) (word-bits 64)
      (failure spans_failure) (allocator spans_allocate spans_release)
      {imports} {functions})'''


def functions():
    out = []
    for e, (_, value, zero) in ELEMENTS.items():
        r, w, own = (f"(span {e} {mode})" for mode in ("read", "write", "own"))
        out.append(f'''
 (fn first_{e} (params (values {r})) (returns {e}) (effects)
   (body (return (at values (usize 0)))))
 (fn fill_{e} (params (values {w}) (value {e})) (returns unit) (effects)
   (body (for i values (body (put values i value))) (return)))
 (fn prefix_{e} (params (values {r}) (count usize)) (returns {r} from values) (effects)
   (body (slice part {r} values (usize 0) count) (return part)))
 (fn identity_{e} (params (values {own})) (returns {own}) (effects)
   (body (return values)))
 (fn consume_{e} (params (values {own})) (returns {e}) (effects heap)
   (body (if (eq (len values) (usize 0))
     (then (return {zero})) (else (return (at values (usize 0)))))))
 (fn local_{e} (params) (returns {e}) (effects)
   (body (array values {e} 3) (call fill_{e} values {value})
     (slice middle {w} values (usize 1) (usize 1))
     (borrow readback {r} middle) (copy shared readback)
     (let result {e} (call first_{e} shared)) (end shared) (end readback) (end middle)
     (move moved values) (return result)))
 (fn heap_{e} (params (count usize)) (returns {e}) (effects heap)
   (body (allocate values {e} count
     (then (call fill_{e} values {value})
       (let moved {own} (call identity_{e} values))
       (return (call consume_{e} moved)))
     (else (return {zero})))))
 (fn empty_{e} (params) (returns usize) (effects)
   (body (array values {e} 0) (return (len values))))
''')
    return "\n".join(out)


def controls(work):
    common = '(fn target (params (x (span u32 write))) (returns unit) (effects) (body (return)))'
    cases = {
        "call_element": '(array x u16 2) (call target x) (return)',
        "borrow_element": '(array x u16 2) (borrow b (span u32 read) x) (return)',
        "store_element": '(array x u16 2) (put x (usize 0) (u32 1)) (return)',
        "write_read": '(array x u32 2) (borrow b (span u32 read) x) (call target b) (return)',
        "alias": '(array x u32 2) (borrow a (span u32 write) x) (borrow b (span u32 read) x) (return)',
        "double_move": '(array x u32 2) (move a x) (move b x) (return)',
        "copy_owner": '(array x bool 2) (copy a x) (return)',
        "ended_view": '(array x u64 2) (borrow a (span u64 read) x) (end a) (let v u64 (at a (usize 0))) (return)',
        "free_borrowed": '(array x u32 2) (borrow a (span u32 read) x) (drop x) (return)',
        "array_size_overflow": '(array x u64 1152921504606846976) (return)',
        "unsupported_element": '(array x f32 2) (return)',
        "unsupported_mode": '(borrow a (span u32 exclusive) missing) (return)',
        "guard_namespace": '(array CESTA_CHECKED_u32_SPANS_DEFINED u32 2) (return)',
        "nested_mode": '(array x u32 2) (borrow a (span u32 (span u8 read)) x) (return)',
    }
    for name, body in cases.items():
        source = work / f"bad-{name}.cesta"
        source.write_text(document(common + f'(fn test (params) (returns unit) (effects) (body {body}))'))
        run([BINARY, "checked", "check", source], accepted=False)
    for name, body in {
        "escape": '(array x u32 2) (borrow local_view (span u32 read) x) (return local_view)',
        "return_element": '(return b)',
    }.items():
        source = work / f"bad-{name}.cesta"
        source.write_text(document(f'''(fn test (params (a (span u32 read)) (b (span u16 read)))
          (returns (span u32 read) from a) (effects) (body {body}))'''))
        run([BINARY, "checked", "check", source], accepted=False)
    return len(cases) + 2


def native_views(work, exported):
    declarations, statements = [], []
    for e, (ctype, _, _) in ELEMENTS.items():
        desc = "cesta_read_bytes" if e == "u8" else f"cesta_read_{e}_span"
        declarations += [f'(view {desc} data len read {e})',
                         f'(foreign spans_first_{e} (obligation "Reads the typed initialized span; retains no pointer."))']
        statements.append(f'{ctype} data_{e}[2]={{0,1}}; {desc} view_{e}={{data_{e},2}}; '
                          f'{desc} copy_{e}={{view_{e}.data,view_{e}.len}}; '
                          f'if(spans_first_{e}(copy_{e})!=0)return 1;')
    source = work / 'native.cesta'
    text = '(cesta native-c/1 compact-c/2 (unit (include "spans.h") (ownership (check-all) '
    text += ' '.join(declarations) + ') (native ' + json.dumps('int native_test(void){'+' '.join(statements)+'return 0;}') + ')))'
    source.write_text(text)
    provider = work / 'native-host.c'
    provider.write_text('#include <stdlib.h>\nint native_test(void);\n'
                        'void spans_failure(void){_Exit(73);}\nvoid *spans_allocate(size_t n){return malloc(n);}\n'
                        'void spans_release(void *p){free(p);}\nint main(void){return native_test();}\n')
    for cc in ('gcc','clang'):
        binary = work / f'native-{cc}'
        checked_spans_command = [BINARY,'cc','--cc',cc,'-std=c11','-O2','-I',work]
        run([*checked_spans_command, source, exported, provider, '-o', binary])
        run([binary])
    for label, old, new in (
        ('extent', 'view_u32={data_u32,2}', 'view_u32={data_u32,3}'),
        ('overflow', 'view_u64={data_u64,2}', 'view_u64={data_u64,SIZE_MAX}'),
        ('element','cesta_read_u32_span data len read u32','cesta_read_u32_span data len read u16'),
    ):
        assert old in text
        mutant = work / f'native-{label}.cesta'; mutant.write_text(text.replace(old,new))
        run([BINARY,'cc','--cc','gcc','-std=c11','-I',work,'-fsyntax-only',mutant],accepted=False)


def imported_spans(work, exported):
    relay = work / 'relay.cesta'
    relay.write_text(document('''
      (fn forward (params (values (span u32 read))) (returns u32) (effects)
        (body (return (call All_first_u32 values))))
      (fn transfer (params (values (span u32 own))) (returns (span u32 own)) (effects)
        (body (return (call All_identity_u32 values))))
    ''', name='relay', imports='(import "spans.cesta" as All)'))
    run([BINARY, 'checked', 'header', relay, '-o', work/'relay.h'])
    run([BINARY, 'export-c', relay, '-o', work/'relay.c'])
    host = work/'relay-host.c'
    host.write_text('''#include <assert.h>
#include <stdlib.h>
#include "relay.h"
void spans_failure(void){_Exit(73);}
void *spans_allocate(size_t n){return malloc(n);}
void spans_release(void *p){free(p);}
int main(void){uint32_t data[2]={17,23};assert(relay_forward((cesta_read_u32_span){data,2})==17);
  uint32_t *p=malloc(2*sizeof(*p));assert(p);p[0]=31;p[1]=37;
  cesta_owned_u32_span result=relay_transfer((cesta_owned_u32_span){p,2});
  assert(result.data==p && result.len==2 && result.data[1]==37);free(result.data);return 0;}
''')
    for cc in ('gcc','clang'):
        binary = work/f'relay-{cc}'
        run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I',work,
             exported,work/'relay.c',host,'-o',binary]); run([binary])
    bad = work/'relay-bad.cesta'
    bad.write_text(relay.read_text().replace('values (span u32 read)', 'values (span u16 read)'))
    run([BINARY,'checked','check',bad],accepted=False)
    other = work/'relay-allocator.cesta'
    other.write_text(relay.read_text().replace('(allocator spans_allocate spans_release)',
                                               '(allocator different_allocate different_release)'))
    run([BINARY,'checked','check',other],accepted=False)


def native_owners(work):
    code = '''#include <stdint.h>
#include <stddef.h>
extern void *span_reserve(size_t);
extern void span_dispose(void *);
typedef struct { uint32_t *data; size_t len; } WordBuffer;
int main(void) {
  uint32_t *data = (uint32_t *)span_reserve(3 * sizeof(uint32_t));
  if (!data) { span_dispose(data); return 0; }
  WordBuffer buffer = {data, 3};
  WordBuffer moved = buffer;
  span_dispose(moved.data);
  return 0;
}
'''
    contracts = '''(allocator heap span_reserve span_dispose (obligation "Fresh aligned allocation and matching release; null is permitted."))
      (view WordBuffer data len write u32 (owns heap))'''
    text = '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) '+contracts+') (native '+json.dumps(code)+')))'
    source = work/'native-owner.cesta'; source.write_text(text)
    provider = work/'native-allocator.c'
    provider.write_text('#include <stdlib.h>\nvoid *span_reserve(size_t n){return malloc(n);}\n'
                        'void span_dispose(void *p){free(p);}\n')
    for cc in ('gcc','clang'):
        binary = work/f'native-owner-{cc}'
        run([BINARY,'cc','--cc',cc,'-std=c11','-O2',source,provider,'-o',binary]); run([binary])
    for name, old, new in (
        ('double-move','span_dispose(moved.data);','WordBuffer copied = buffer; span_dispose(copied.data); span_dispose(moved.data);'),
        ('short-owner','{data, 3}','{data, 2}'),
    ):
        mutated = work/f'native-owner-{name}.cesta'; mutated.write_text(text.replace(old,new))
        run([BINARY,'cc','--cc','gcc','-std=c11','-fsyntax-only',mutated],accepted=False)


def runtime():
    assertions = []
    for e, (ctype, value, _) in ELEMENTS.items():
        value = "true" if e == "bool" else "UINT64_C(" + value.split()[1][:-1] + ")"
        desc = "cesta_read_bytes" if e == "u8" else f"cesta_read_{e}_span"
        assertions.append(f'''
    assert(spans_local_{e}() == {value});
    assert(spans_empty_{e}() == 0);
    allocations = releases = attempts = 0;
    assert(spans_heap_{e}(3) == {value});
    assert(allocations == 1 && releases == 1 && last_size == 3 * sizeof({ctype}));
    allocations = releases = attempts = 0;
    assert(spans_heap_{e}(0) == 0 && attempts == 0 && releases == 0);
    failing = 1; assert(spans_heap_{e}(3) == 0); failing = 0;
    attempts = 0; assert(spans_heap_{e}(SIZE_MAX) == 0 && attempts == 0);
    {ctype} input_{e}[2] = {{0, ({ctype}){value}}};
    {desc} empty_{e} = spans_prefix_{e}(({desc}){{NULL, 0}}, 0);
    assert(empty_{e}.data == NULL && empty_{e}.len == 0);
    {desc} p_{e} = spans_prefix_{e}(({desc}){{input_{e}, 2}}, 1);
    assert(p_{e}.data == input_{e} && p_{e}.len == 1);
''')
    return r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "spans.h"
static size_t allocations, releases, attempts, last_size;
static int failing;
static void *misaligned_allocation;
void spans_failure(void) { _Exit(73); }
void *spans_allocate(size_t n) { ++attempts; last_size=n; if(failing==1)return NULL; ++allocations;
    if(failing==2){misaligned_allocation=malloc(n+1);return (unsigned char *)misaligned_allocation+1;}return malloc(n); }
void spans_release(void *p) { ++releases; if(misaligned_allocation){free(misaligned_allocation);misaligned_allocation=NULL;}else free(p); }
int main(int argc,char **argv) {
    if(argc>1) {
        uint32_t values[2]={3,7};
        if(!strcmp(argv[1],"bounds")) return (int)spans_first_u32((cesta_read_u32_span){values,0});
        if(!strcmp(argv[1],"null")) return (int)spans_first_u32((cesta_read_u32_span){NULL,1});
        if(!strcmp(argv[1],"extent")) return (int)spans_first_u32((cesta_read_u32_span){values,SIZE_MAX});
        if(!strcmp(argv[1],"slice")) return (int)spans_prefix_u32((cesta_read_u32_span){values,2},3).len;
        return 2;
    }
''' + "\n".join(assertions) + '''
    allocations = releases = attempts = 0;
    failing=2; assert(spans_heap_u64(3)==0); failing=0;
    assert(allocations==1 && releases==1 && misaligned_allocation==NULL);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sanitizers", action="store_true")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="checked-spans-", dir=ROOT / "build"))
    source = work / "spans.cesta"
    source.write_text(document(functions()))
    header, exported, plan = work / "spans.h", work / "spans.c", work / "spans.plan"
    run([BINARY, "checked", "check", source])
    run([BINARY, "checked", "header", source, "-o", header])
    run([BINARY, "export-c", source, "--plan", plan, "-o", exported, "--"])
    assert '"cesta-permissions/2"' in plan.read_text()
    host = work / "host.c"; host.write_text(runtime())
    refused = controls(work)
    native_views(work, exported)
    imported_spans(work, exported)
    native_owners(work)
    builds = 0
    for cc in ("gcc", "clang"):
        binaries = []
        for authored in (False, True):
            output = work / f"{cc}-{authored}"
            command = [cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", work]
            if authored: command = [BINARY, "cc", "--cc", cc, *command[1:]]
            run([*command, source if authored else exported, host, "-o", output])
            run([output]); builds += 1
            binaries.append(output)
            for failure in ("bounds", "null", "extent", "slice"):
                assert run([output, failure], accepted=False).returncode == 73
        assert binaries[0].read_bytes() == binaries[1].read_bytes(), (cc, "export identity",
            [hashlib.sha256(binary.read_bytes()).hexdigest() for binary in binaries])
        if args.sanitizers:
            output = work / f"{cc}-san"
            run([cc, "-std=c11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                 "-fno-pie", "-no-pie", "-I", work, exported, host, "-o", output])
            run([output]); builds += 1
    print(f"typed spans: 6 scalar types, {builds} native/export builds, {refused} source refusals, bounds/allocation controls passed")
    (work / "summary.json").write_text(json.dumps({"types":list(ELEMENTS), "builds":builds,"refused":refused})+"\n")


if __name__ == "__main__":
    main()
