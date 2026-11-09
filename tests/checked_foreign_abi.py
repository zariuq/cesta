"""Foreign span projections against authoritative C prototypes and real calls."""

import argparse
import json
from pathlib import Path
import tempfile

import checked_spans as spans


def flat_fire_client(work, directory):
    directory=directory.resolve()
    source=work/'leaf_call.cesta'
    source.write_text(spans.document('''
      (foreign fire flat_fire
        (params (fields (span u32 read) data) (rows (span u32 read) data)
                (guards (span u32 read) data_len) (makes (span u32 read) data_len)
                (destination (span u32 write) data) (scratch (span u32 write) data))
        (returns u64) (effects)
        (obligation "Requires valid packed selectors, readable inputs, sufficient outputs and the leaf's frame exclusions; retains no pointer."))
      (fn run (params (fields (span u32 read)) (rows (span u32 read))
                      (guards (span u32 read)) (makes (span u32 read))
                      (destination (span u32 write)) (scratch (span u32 write)))
        (returns u64) (effects)
        (body (require (le (len makes) (len destination)))
              (require (le (len makes) (len scratch)))
              (return (call fire fields rows guards makes destination scratch))))''',name='leaf_call'))
    header=work/'leaf_call.h';exported=work/'leaf_call.c';host=work/'leaf_host.c'
    spans.run([spans.BINARY,'checked','header',source,'-o',header])
    spans.run([spans.BINARY,'export-c',source,'-o',exported,'--plan',work/'leaf_call.plan'])
    host.write_text('''#include "leaf_call.h"
#include <assert.h>
#include <stdlib.h>
void spans_failure(void){abort();}
int main(void){
  uint32_t fields[4]={3,7,11,7},rows[2]={0,2},guards[1]={0x01010100},makes[2]={1,256};
  uint32_t dest[3]={91,92,93},scratch[3]={81,82,83};
  cesta_read_u32_span f={fields,4},r={rows,2},g={guards,1},m={makes,2};
  cesta_write_u32_span d={dest,3},s={scratch,3};
  assert(leaf_call_run(f,r,g,m,d,s)==1);
  assert(dest[0]==11 && dest[1]==7 && dest[2]==93 && scratch[2]==83);
  fields[3]=9;
  assert(leaf_call_run(f,r,g,m,d,s)==0);
  assert(dest[0]==11 && dest[1]==7 && dest[2]==93 && scratch[0]==11 && scratch[1]==7 && scratch[2]==83);
  assert(leaf_call_run((cesta_read_u32_span){0,0},(cesta_read_u32_span){0,0},
    (cesta_read_u32_span){0,0},(cesta_read_u32_span){0,0},
    (cesta_write_u32_span){0,0},(cesta_write_u32_span){0,0})==1);
  return 0;
}
''')
    for cc in ('gcc','clang'):
        leaf=work/f'flat_fire-{cc}.o'
        spans.run([cc,'-c',directory/'flat_fire.S','-o',leaf])
        for sanitizer in (False,True):
            flags=['-std=c11','-O2','-Wall','-Wextra','-Werror','-I',work,'-include',directory/'flat_fire.h']
            if sanitizer:flags+=['-fsanitize=address,undefined','-fno-pie','-no-pie']
            for native in (False,True):
                output=work/f'leaf-{cc}-{sanitizer}-{native}'
                driver=[cc] if native else [spans.BINARY,'cc','--cc',cc]
                spans.run([*driver,*flags,exported if native else source,host,leaf,'-o',output])
                spans.run([output])
    print('flat_fire foreign ABI: 24 accept/refuse/empty calls through generated/native C; authoritative header and real assembly')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--flat-fire',type=Path,help='qualify against this external flat_fire.h/flat_fire.S')
    args=parser.parse_args()
    work=Path(tempfile.mkdtemp(prefix='foreign-span-abi-',dir=spans.ROOT/'build'))
    declarations=[]; prototypes=[]; implementations=[]; exercises=[]
    for e,(ctype,_,_) in spans.ELEMENTS.items():
        read='read' if e=='u8' else f'(span {e} read)'
        write='write' if e=='u8' else f'(span {e} write)'
        declarations.append(f'''
          (foreign f_{e} native_{e}
            (params (source {read} data) (destination {write} data_len))
            (returns usize) (effects)
            (obligation "Reads source[0], writes the final destination element; retains no pointer."))
          (fn run_{e} (params (source {read}) (destination {write})) (returns usize) (effects)
            (body (require (gt (len source) (usize 0)))
              (require (gt (len destination) (usize 0)))
              (return (call f_{e} source destination))))''')
        prototypes.append(f'size_t native_{e}(const {ctype} *, {ctype} *, size_t);')
        implementations.append(f'''size_t native_{e}(const {ctype} *source, {ctype} *destination, size_t n) {{
            destination[n-1]=source[0]; return n; }}''')
        suffix='bytes' if e=='u8' else e+'_span'
        exercises.append(f'''{{ {ctype} source[1]={{1}}, destination[3]={{0}};
          if (foreign_calls_run_{e}((cesta_read_{suffix}){{source,1}},
              (cesta_write_{suffix}){{destination,3}})!=3 || destination[0] || destination[1] || !destination[2]) return 1;
        }}''')
    source=work/'calls.cesta';source.write_text(spans.document('\n'.join(declarations),name='foreign_calls'))
    native=work/'native.h';native.write_text('#pragma once\n#include <stddef.h>\n#include <stdint.h>\n#include <stdbool.h>\n'+'\n'.join(prototypes)+'\n')
    exported=work/'calls.c';header=work/'calls.h'
    spans.run([spans.BINARY,'export-c',source,'-o',exported,'--plan',work/'calls.plan'])
    spans.run([spans.BINARY,'checked','header',source,'-o',header])
    evidence=json.loads(spans.run([spans.BINARY,'checked','contract',source]).stdout)
    assert len(evidence['foreign'])==6
    assert all([p['projection'] for p in row['params']]==['data','data_len'] for row in evidence['foreign'])
    provider=work/'provider.c';provider.write_text('#include "native.h"\n#include "calls.h"\n#include <stdlib.h>\n'+
        'void spans_failure(void){abort();}\n'+'\n'.join(implementations)+
        '\nint main(void){'+'\n'.join(exercises)+'return 0;}\n')
    runs=0
    for cc in ('gcc','clang'):
        for sanitizer in (False,True):
            flags=['-std=c11','-O2','-Wall','-Wextra','-Werror','-I',work,'-include',native]
            if sanitizer: flags+=['-fsanitize=address,undefined','-fno-pie','-no-pie']
            output=work/f'{cc}-{sanitizer}'
            spans.run([spans.BINARY,'cc','--cc',cc,*flags,source,provider,'-o',output]);spans.run([output]);runs+=6
            spans.run([cc,*flags,exported,provider,'-o',output]);spans.run([output]);runs+=6
        # The real prototype, rather than a parallel shadow declaration, rejects ABI drift.
        changed=work/'wrong-projection.cesta'
        changed.write_text(source.read_text().replace('(destination (span u32 write) data_len)',
                                                     '(destination (span u32 write) data)'))
        spans.run([spans.BINARY,'cc','--cc',cc,'-std=c11','-include',native,'-fsyntax-only',changed],accepted=False)
        drift=work/'drift.h';drift.write_text(native.read_text().replace('native_u32(const uint32_t *','native_u32(const uint16_t *'))
        spans.run([spans.BINARY,'cc','--cc',cc,'-std=c11','-include',drift,'-fsyntax-only',source],accepted=False)
    for name,before,after in (
        ('scalar','(source (span u32 read) data)','(source u32 data)'),
        ('owned','(source (span u32 read) data)','(source (span u32 own) data)'),
        ('unknown','(source (span u32 read) data)','(source (span u32 read) raw)'),
        ('defined','(fn run_u32 (params (source (span u32 read))',
                   '(fn run_u32 (params (source (span u32 read) data)'),
    ):
        assert before in source.read_text()
        bad=work/(name+'.cesta');bad.write_text(source.read_text().replace(before,after))
        spans.run([spans.BINARY,'checked','check',bad],accepted=False)
    # Projection changes the foreign C ABI, not the abstract span permission signature.
    default=work/'default.cesta';default.write_text(source.read_text().replace(' data_len)',')'))
    assert spans.run([spans.BINARY,'export-c',default]).stdout==exported.read_bytes()
    alias=work/'alias.cesta'
    alias.write_text(spans.document('''
      (foreign f f_native (params (a (span u32 read) data) (b (span u32 write) data))
        (returns unit) (effects) (obligation "Reads a and writes b; retains no pointer."))
      (fn bad (params (a (span u32 write))) (returns unit) (effects)
        (body (call f a a) (return)))'''))
    spans.run([spans.BINARY,'checked','check',alias],accepted=False)
    print(f'foreign span ABI: {runs} native/export calls; 5 source refusals; 4 header/projection drift refusals; default ABI preserved')
    if args.flat_fire:flat_fire_client(work,args.flat_fire)


if __name__=='__main__':main()
