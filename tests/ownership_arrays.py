"""Pointwise reference-record arrays use their C extents and ordinary loans."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent

C_TYPES = '''typedef struct Label {const char *text;} Label;
typedef struct Reader {const int *item;} Reader;
typedef struct Labels {Label items[3];} Labels;
typedef struct Owner {int *item;} Owner;
typedef struct Owners {Owner items[2];} Owners;
typedef struct Ticket {unsigned serial;} Ticket;
typedef struct Handles {const Ticket *item;} Handles;
typedef struct ReadHandle {int *item;} ReadHandle;
'''

PRELUDE = '''(cesta native-c/1 compact-c/2 (unit
 (ownership (check-all)
  (record Label (field text (static nullable)))
  (record Reader (field item (borrow read nullable)))
  (record Labels)
  (record Owner (field item (owns heap nullable)))
  (record Owners)
  (capability Ticket effects)
  (record Handles (field item (static nullable)))
  (record ReadHandle (field item (borrow read nullable)))
  (allocator heap malloc free (obligation "Allocates a fresh extent or null; release consumes its unique authority."))
  CONTRACT)
 (native "#include <stddef.h>\n#include <stdlib.h>\n")
 (native "TYPES
BODY")))'''.replace('TYPES', C_TYPES)

CASES = [
    ('static_local', '(function example (returns-static nullable))',
     'const char *example(void){Label a[3]={{"one"},{"two"},{0}};return a[1].text;}', True),
    ('static_input', '(function example (returns-static nullable))',
     'const char *example(const Label (*a)[3]){return (*a)[1].text;}', True),
    ('zero_array', '(function example (returns-static nullable))',
     'const char *example(void){Label a[17]={0};return a[16].text;}', True),
    ('partial_array', '(function example (returns-static nullable))',
     'const char *example(void){Label a[5]={{"one"}};return a[4].text;}', True),
    ('nested_matrix', '(function example (returns-static nullable))',
     'const char *example(void){Label a[2][2]={{{"one"},{"two"}},{{0},{0}}};return a[0][1].text;}', True),
    ('nested_copy', '(function example (returns-static nullable))',
     'const char *example(void){Labels a={{{"one"},{"two"},{0}}};Labels b=a;return b.items[1].text;}', True),
    ('readers', '', 'int example(void){int x=17;Reader a[2]={{&x},{&x}};return *a[0].item+*a[1].item;}', True),
    ('shared_call', '', 'int total(const Reader (*a)[2]){if(!(*a)[0].item || !(*a)[1].item)return 0;return *(*a)[0].item+*(*a)[1].item;}int example(void){int x=17;Reader a[2]={{&x},{&x}};return total(&a);}', True),
    ('uniform_shared', '', 'int example(size_t i){if(i>=2)return 0;int x=17;Reader a[2]={{&x},{&x}};return *a[i].item;}', True),
    ('uniform_handle_rights', '', 'int example(size_t i){if(i>=2)return 0;int x=17;ReadHandle a[2]={{&x},{&x}};return *a[i].item;}', True),
    ('uniform_static', '(function example (returns-static nullable))', 'const char *example(size_t i){if(i>=3)return 0;Label a[3]={{"a"},{"b"},{0}};return a[i].text;}', True),
    ('uniform_result', '(function example (returns-borrow 0 nullable))', 'const int *example(const int *p,size_t i){if(i>=2)return 0;Reader a[2]={{p},{p}};return a[i].item;}', True),
    ('uniform_referent_lifetime', '', 'int example(size_t i){if(i>=2)return 0;int x=17;const int *p=0;{Reader a[2]={{&x},{&x}};p=a[i].item;}return *p;}', True),
    ('static_unknown_copy', '(function example (returns-static nullable))', 'const char *example(void){Label a[3]={{"a"},{"b"},{0}};Label b[3]={0};for(size_t i=0;i<3;++i)b[i]=a[i];return b[0].text;}', True),
    ('static_unknown_store', '(function example (returns-static nullable))', 'const char *example(void){Label a[3]={0};for(size_t i=0;i<3;++i)a[i].text="a";return a[0].text;}', True),
    ('static_unknown_call', '(function example (returns-static nullable))', 'void set_label(Label *out){out->text="a";}const char *example(void){Label a[3]={0};for(size_t i=0;i<3;++i)set_label(&a[i]);return a[0].text;}', True),
    ('dangling_static', '', 'void example(void){char x=0;Label a[2]={{0},{0}};a[0].text=&x;}', False),
    ('escaping_reader', '', 'void example(void){Reader a[2]={{0},{0}};{int x=1;a[0].item=&x;}}', False),
    ('unknown_alias', '', 'void example(size_t i){Label a[3]={{0},{0},{0}};const Label *held=&a[i];a[0].text="bad";(void)held->text;}', False),
    ('different_origins', '', 'int example(size_t i){if(i>=2)return 0;int x=1,y=2;Reader a[2]={{&x},{&y}};return *a[i].item;}', False),
    ('uniform_rights_bypass', '', 'void example(size_t i){if(i>=2)return;int x=1;ReadHandle a[2]={{&x},{&x}};*a[i].item=9;}', False),
    ('nullable_cross_index', '', 'int example(size_t i){if(i>=2)return 0;int x=1;Reader a[2]={{&x},{0}};if(a[0].item)return *a[i].item;return 0;}', False),
    ('nullable_changed_index', '', 'int example(size_t i){if(i>=2)return 0;int x=1;Reader a[2]={{&x},{0}};if(a[i].item){i=1;return *a[i].item;}return 0;}', False),
    ('opaque_array_erasure', '', 'void example(const Handles (*a)[2],size_t i){if(i>=2)return;const void *erased=(*a)[i].item;(void)erased;}', False),
    ('unknown_static_copy_alias', '', 'int example(size_t i){if(i>=2)return 0;Label a[2]={{"x"},{0}};a[0]=a[i];return *a[0].text;}', False),
    ('unknown_static_store_local', '', 'void example(size_t i){if(i>=2)return;char x=0;Label a[2]={0};a[i].text=&x;}', False),
    ('self_copy_uninitialized_field', '', 'void example(void){Label a;a=a;}', False),
    ('self_copy_under_borrow', '', 'int example(void){Label a={"a"};const Label *held=&a;a=a;return held->text!=0;}', False),
    ('overlap', '', 'void example(void){Label a[3]={{0},{0},{0}};Label *held=&a[0];a[0].text="bad";(void)held->text;}', False),
    ('out_of_range', '(function example (returns-static nullable))',
     'const char *example(void){Label a[3]={{0},{0},{0}};return a[3].text;}', False),
    ('owner_move', '', 'void example(void){int *p=malloc(sizeof(int));if(!p){free(p);return;}Owners a={{{p},{0}}};Owners b=a;free(b.items[0].item);}', True),
    ('double_move', '', 'void example(void){int *p=malloc(sizeof(int));if(!p){free(p);return;}Owners a={{{p},{0}}};Owners b=a;Owners c=a;free(b.items[0].item);free(c.items[0].item);}', False),
    ('move_while_borrowed', '', 'int example(void){int *p=malloc(sizeof(int));if(!p){free(p);return 0;}*p=7;Owners a={{{p},{0}}};const int *held=a.items[0].item;Owners b=a;int result=*held;free(b.items[0].item);return result;}', False),
    ('unknown_owner_move', '', 'void example(size_t i){int *p=malloc(sizeof(int));if(!p){free(p);return;}Owners a={{{p},{0}}};Owner b=a.items[i];free(b.item);}', False),
]

RUNTIME = {
    'static_local': 'return example()[0]!=\'t\';',
    'static_input': 'Label a[3]={{"a"},{"b"},{0}};return example(&a)[0]!=\'b\';',
    'zero_array': 'return example()!=0;',
    'partial_array': 'return example()!=0;',
    'nested_matrix': 'return example()[0]!=\'t\';',
    'nested_copy': 'return example()[0]!=\'t\';',
    'readers': 'return example()!=34;',
    'shared_call': 'return example()!=34;',
    'uniform_shared': 'return example(0)!=17 || example(1)!=17 || example(2)!=0;',
    'uniform_handle_rights': 'return example(0)!=17 || example(1)!=17 || example(2)!=0;',
    'uniform_static': 'return example(0)[0]!=\'a\' || example(1)[0]!=\'b\' || example(2)!=0 || example(3)!=0;',
    'uniform_result': 'int n=17;return example(&n,0)!=&n || example(&n,1)!=&n || example(&n,2)!=0;',
    'uniform_referent_lifetime': 'return example(0)!=17 || example(1)!=17 || example(2)!=0;',
    'static_unknown_copy': 'return example()[0]!=\'a\';',
    'static_unknown_store': 'return example()[0]!=\'a\';',
    'static_unknown_call': 'return example()[0]!=\'a\';',
    'owner_move': 'example();return 0;',
}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cesta', type=Path, required=True)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/ownership-arrays')
    ap.add_argument('--runtime', action='store_true')
    ap.add_argument('--lean-root', type=Path); ap.add_argument('--admit', action='store_true'); args = ap.parse_args()
    if args.admit and args.lean_root is None: args.lean_root = ROOT.parent / 'Mettapedia/lean/mettapedia'
    w = args.output.resolve(); w.mkdir(parents=True, exist_ok=True); records = []; plans = {}
    for name, contract, body, accepted in CASES:
        text = PRELUDE.replace('CONTRACT', contract).replace('BODY', body.replace('"', '\\"'))
        source = w / (name + '.cesta'); source.write_text(text)
        for cc in ('gcc', 'clang'):
            out = w / (name + '-' + cc + '.c'); plan = out.with_suffix('.plan')
            result = subprocess.run([str(args.cesta.resolve()), 'export-c', str(source), '--cc', cc, '--std=c11', '-o', str(out), '--plan', str(plan)], capture_output=True)
            assert (result.returncode == 0) == accepted, (name, cc, result.stderr.decode()[-4000:])
            if accepted:
                subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsyntax-only', str(out)], check=True)
                plans[hashlib.sha256(plan.read_bytes()).hexdigest()] = plan
            else: assert b'ownership' in result.stderr, (name, result.stderr)
            records.append(dict(case=name, compiler=cc, accepted=accepted,
                                plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest() if accepted else None))
    admission = []
    if args.lean_root:
        native = importlib.import_module('ownership_admission')
        for i, plan in enumerate(plans.values()):
            record, = native.parse(plan.read_bytes())
            admission.append(native.admit_record(record, w / f'Array{i}.lean', args.lean_root.resolve()))
    runtime = []
    if args.runtime:
        prefix = '#include <stddef.h>\n#include <stdlib.h>\n' + C_TYPES
        for name, contract, body, accepted in CASES:
            if not accepted: continue
            assert name in RUNTIME, name
            harness = '\nint main(void){' + RUNTIME[name] + '}\n'
            for cc in ('gcc', 'clang'):
                for label, flags in [('O0', ['-O0']), ('O2', ['-O2']), ('SAN', ['-O1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie'])]:
                    binaries = []
                    for route, text in [('plain', prefix + body + harness), ('checked', (w / (name + '-' + cc + '.c')).read_text() + harness)]:
                        directory = w / 'runtime' / name / cc / label / route; directory.mkdir(parents=True, exist_ok=True)
                        source = directory / 'unit.c'; source.write_text(text); binary = directory / 'run'
                        subprocess.run([cc, '-std=c11', *flags, str(source), '-o', str(binary)], check=True)
                        result = subprocess.run([str(binary)], capture_output=True,
                                                env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1'})
                        assert result.returncode == 0, (name, cc, label, route, result.stderr.decode()[-3000:])
                        binaries.append(binary.read_bytes())
                    if label != 'SAN': assert binaries[0] == binaries[1], (name, cc, label, 'annotation erasure changed binary')
                    runtime.append(dict(case=name, compiler=cc, mode=label, routes=2, identical_binary=label != 'SAN'))
    (w / 'summary.json').write_text(json.dumps(dict(cases=records, admission=admission, runtime=runtime), indent=2) + '\n')
    print('Reference arrays:', sum(c[3] for c in CASES), 'positive cases;', sum(not c[3] for c in CASES), 'refusals; GCC and Clang')


if __name__ == '__main__': main()
