"""Member references retain their declaring record and source provenance."""

import argparse
import json
from pathlib import Path
import subprocess

from provenance_controls import metta

ROOT = Path(__file__).resolve().parent.parent


def run(arguments, accepted=True):
    result = subprocess.run(list(map(str, arguments)), cwd=ROOT, capture_output=True)
    assert (result.returncode == 0) == accepted, (arguments, result.returncode, result.stderr.decode(errors='replace'))
    return result


def atom(value):
    if value['kind'] == 'list':
        return [atom(item) for item in value['items']]
    return bytes.fromhex(value['bytes']) if 'bytes' in value else value['value']


def has_part(value, name):
    return isinstance(value, list) and (bool(value) and value[0] == name or
                                      any(has_part(item, name) for item in value))


def rows(binary, source, compiler, *flags, ccflags=()):
    output = run([binary, 'bind', '--fields', '--json-lines', *flags,
                  '--cc', compiler, '-std=gnu11', *ccflags, source]).stdout
    return [atom(json.loads(line)['record']) for line in output.splitlines()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cesta', type=Path, default=ROOT / 'build/cesta')
    parser.add_argument('--work', type=Path, default=ROOT / 'build/field-references')
    args = parser.parse_args()
    binary = args.cesta.resolve()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    header = work / 'fields.h'
    header.write_text('''typedef struct Left { int *item; } Left;
typedef struct Right { float *item; } Right;
struct Outer { int _; union { int scalar; struct { int *promoted; }; }; };
struct Bits { unsigned flags:3; unsigned:0; unsigned other:2; };
struct Cells { Left cells[2]; };
struct Flexible { unsigned count; unsigned char data[]; };
typedef struct {int *value;} Anonymous;
typedef Left *LeftPointer;
typedef Left LeftArray[2];
typedef const Left ConstLeft;
#define FIELD(object) ((object)->item)
''')
    source = work / 'fields.c'
    source.write_text('''#include "fields.h"
int sample(Left *left, Right *right, struct Outer *outer) {
  Left local = {.item=FIELD(left)};
  struct Cells array = {.cells[1].item=left->item};
  struct Bits bits = {.flags=3};
  return *local.item + (int)*right->item + *outer->promoted + bits.flags +
      *array.cells[1].item + __builtin_offsetof(struct Outer,promoted) +
      __builtin_offsetof(struct Cells,cells[1].item);
}
int nested(void) {
  struct Left { long item; }; struct Left local={.item=7};return (int)local.item;
}
''')
    projections = []
    for compiler in ('gcc', 'clang'):
        run([compiler, '-std=gnu11', '-fsyntax-only', source])
        result = rows(binary, source, compiler)
        entities = {int(row[1]): row for row in result if row[0] == 'entity'}
        fields = {(int(row[1]), int(row[2])): row for row in result if row[0] == 'field'}
        uses = [row for row in result if row[0] == 'field-use']
        item_owners = {key[0] for key, row in fields.items() if row[3] == 'item'}
        assert len(item_owners) == 3, item_owners
        aliases = {row[3]:int(row[2]) for row in result if row[0] == 'record-alias'}
        assert aliases['Left'] in item_owners and aliases['Right'] in item_owners
        assert aliases['ConstLeft'] == aliases['Left'] and 'Anonymous' in aliases
        assert 'LeftPointer' not in aliases and 'LeftArray' not in aliases
        assert any(row[0] == 'record-type' and int(row[1]) == aliases['Anonymous'] and row[3] == [] for row in result)
        assert {fields[key][4] for key in fields if key[0] in item_owners} >= {'int *', 'float *', 'long'}
        assert {'dot', 'arrow', 'initializer', 'offsetof'} <= {row[5] for row in uses}
        for use in uses:
            key = (int(use[2]), int(use[3]))
            assert key in fields and fields[key][3] == use[6], use
            assert int(use[4]) in entities, use
            assert any(isinstance(item, list) and item[0] == 'at' for item in use), use
        promoted = [row for row in uses if row[6] == 'promoted']
        assert len(promoted) == 2 and all(row[2] != row[4] for row in promoted), promoted
        assert len({(row[2], row[3]) for row in promoted}) == 1
        assert any(row[5] == 'offsetof' and row[6] == 'cells' for row in uses)
        assert any(row[5] == 'initializer' and row[6] == 'cells' for row in uses)
        assert any(row[3] == '_' for row in fields.values())
        assert any(row[3] == [] and ['anonymous'] in row for row in fields.values())
        assert any(row[3] == 'flags' and ['bits', '0', '3'] in row for row in fields.values())
        assert any(row[3] == 'data' and ['incomplete-array'] in row for row in fields.values())
        assert all((['references'] in row) == (row[4] != 'long')
                   for row in fields.values() if row[3] == 'item')
        assert any(row[3] == 'cells' and ['references'] in row for row in fields.values())
        assert not any(row[3] == 'flags' and ['references'] in row for row in fields.values())
        assert any(has_part(row, 'from') for row in uses)
        plain = run([binary, 'bind', '--cc', compiler, '-std=gnu11', source]).stdout
        assert b'(field ' not in plain and b'(field-use ' not in plain
        projections.append(sorted((entities[int(row[2])][3] or '<anonymous>', row[5], row[6]) for row in uses))
        raw = run([binary, 'bind', '--fields', '--cc', compiler, '-std=gnu11', source]).stdout
        encoded = run([binary, 'bind', '--fields', '--json-lines', '--cc', compiler, '-std=gnu11', source]).stdout
        records = [json.loads(line)['record'] for line in encoded.splitlines()]
        restored = b'(bindings ' + b' '.join(metta(item) for item in records) + b')'
        original_file, restored_file = work/'raw.metta', work/'restored.metta'
        original_file.write_bytes(raw); restored_file.write_bytes(restored)
        assert run([binary, 'pack', original_file]).stdout == run([binary, 'pack', restored_file]).stdout
        # A field spelling in another record must not be mistaken for one here.
        bad = work / 'missing.c'
        bad.write_text('struct A{int first;};struct B{int item;};int f(struct A*a){return a->item;}\n')
        run([compiler, '-std=c11', '-fsyntax-only', bad], accepted=False)
        refused = run([binary, 'bind', '--fields', '--json-lines', '--cc', compiler, '-std=c11', bad], accepted=False)
        assert not refused.stdout and b'no member named item' in refused.stderr
    assert projections[0] == projections[1]
    # The field facts follow the selected ABI, rather than the host pointer size.
    abi_source = work / 'abi.c'
    abi_source.write_text('struct P{char tag;void *data;};void *f(struct P*p){return p->data;}\n')
    for abi, width in [('x86_64-sysv-lp64', '8'), ('i386-sysv-ilp32', '4'), ('aarch64-aapcs64-lp64', '8')]:
        result = rows(binary, abi_source, 'gcc', '--abi='+abi)
        field = next(row for row in result if row[0] == 'field' and row[3] == 'data')
        assert field[5:8] == [width, width, width], (abi, field)
    inferred = rows(binary, abi_source, 'gcc', ccflags=('-m32',))
    assert next(row for row in inferred if row[0] == 'field-profile')[2] == '4'
    inferred = rows(binary, abi_source, 'clang', ccflags=('--target=aarch64-unknown-linux-gnu',))
    assert next(row for row in inferred if row[0] == 'field-profile')[1] == 'aarch64-aapcs64-lp64'
    invalid = run([binary, 'types', '--fields', '--cc', 'gcc', source], accepted=False)
    assert invalid.returncode == 2 and b'--fields applies to bind' in invalid.stderr
    # Inventory mode does not claim to check a function body or its references.
    inventory = work/'inventory.c'
    inventory.write_text('struct Header{int field;};int f(struct Header*h){return h->missing;}\n')
    run([binary,'bind','--fields','--cc','gcc',inventory],accepted=False)
    declarations = rows(binary, inventory, 'gcc', '--declarations-only')
    assert ['field-scope','declarations'] in declarations
    assert not any(row[0] == 'field-use' for row in declarations)
    inventory.write_text('struct Header{int field;};int f(void){struct Local{int item;};return unbound;}\n')
    declarations = rows(binary,inventory,'gcc','--declarations-only')
    assert not any(row[0]=='record-type' and row[3]=='Local' for row in declarations)
    assert not any(row[0]=='use' and row[2]=='unbound' for row in declarations)
    run([binary,'bind','--fields','--cc','gcc',inventory],accepted=False)
    invalid = run([binary,'bind','--declarations-only','--cc','gcc',source],accepted=False)
    assert invalid.returncode == 2
    # Escaped and literal spellings resolve to one member, not two names.
    unicode_source = work/'unicode.c'
    unicode_source.write_text('struct Unicode{int caf\\u00e9;};int f(struct Unicode*u){return u->café;}\n')
    unicode_rows = rows(binary,unicode_source,'gcc')
    assert any(row[0]=='field-use' and row[6]=='café' for row in unicode_rows)
    (work / 'summary.json').write_text(json.dumps({'compilers':['gcc','clang'], 'abi_profiles':3,
        'pointer_spelling_disambiguation':True,'anonymous_promotion':True,'initializers_and_offsetof':True,
        'provenance':True,'invalid_member_refused':True}, indent=2)+'\n')
    print('field references: GCC/Clang; record-qualified names, anonymous promotion, initializers, offsetof, '
          'bit-fields, flexible arrays, aliases, provenance, exact JSON, inventory scope, '
          'target inference, three ABIs and missing-member refusals', flush=True)


if __name__ == '__main__':
    main()
