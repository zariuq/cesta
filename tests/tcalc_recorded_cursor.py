"""Actual tcalc recorded-candidate provider: contracts, ABI and native controls."""

import argparse
import copy
import json
from pathlib import Path
import tempfile

from checked_spans import BINARY, ROOT, run
from oracles.pro_compact_cesta import Sym, Text, dumps, parse


HOST = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "bridge/tc_cetta_bridge.h"

int main(void) {
  TcPreparedStep candidates[64] = {{0}};
  Occ take[64], keep[64]; Term inst[64], seeds[64];
  for (unsigned i=0; i<64; ++i) {
    take[i]=UINT32_MAX-i; keep[i]=UINT32_C(0x80000000)+i;
    inst[i]=UINT32_MAX-3*i; seeds[i]=UINT32_MAX-7*i;
    candidates[i]=(TcPreparedStep){(int)i, &take[i], &keep[i], &inst[i],
                                   UINT32_MAX-i, 1, &seeds[i]};
  }
  unsigned cases=0;
  size_t null_position=0;
  if (tc_cetta_cursor_advance(NULL,&null_position,1) || null_position ||
      tc_cetta_cursor_advance(&candidates[0],NULL,1)) return 1;
  for (size_t len=0; len<=64; ++len) {
    for (size_t start=0; start<=len+2; ++start) {
      TcCettaRecordedStream stream={candidates,len,start};
      for (unsigned step=0; step<4; ++step) {
        size_t previous=stream.pos;
        const TcPreparedStep *expected=previous<len ? &candidates[previous] : NULL;
        const TcPreparedStep *actual=tc_cetta_cursor_advance(expected,&stream.pos,stream.len);
        if (actual!=expected || stream.pos!=previous+(expected!=NULL) ||
            stream.candidates!=candidates || stream.len!=len) return 2;
        if (actual && (actual->rule!=(int)previous || actual->take!=&take[previous] ||
            actual->keep!=&keep[previous] || actual->inst!=&inst[previous] ||
            actual->seeds!=&seeds[previous] || actual->dest!=UINT32_MAX-previous ||
            *actual->take!=UINT32_MAX-previous ||
            *actual->keep!=UINT32_C(0x80000000)+previous)) return 3;
        ++cases;
      }
    }
  }
  TcCettaRecordedStream null_items={NULL,64,0};
  if (tc_cetta_cursor_advance(NULL,&null_items.pos,64) || null_items.pos) return 4;
  TcCettaRecordedStream exhausted={candidates,64,SIZE_MAX};
  if (tc_cetta_cursor_advance(candidates,&exhausted.pos,64) || exhausted.pos!=SIZE_MAX) return 5;
  exhausted.len=SIZE_MAX;
  if (tc_cetta_cursor_advance(candidates,&exhausted.pos,SIZE_MAX) || exhausted.pos!=SIZE_MAX) return 6;
  exhausted.pos=SIZE_MAX-1;
  if (tc_cetta_cursor_advance(candidates,&exhausted.pos,SIZE_MAX)!=candidates ||
      exhausted.pos!=SIZE_MAX) return 8;
  for (unsigned i=0; i<64; ++i) {
    if (candidates[i].rule!=(int)i || candidates[i].take!=&take[i] ||
        take[i]!=UINT32_MAX-i || candidates[i].keep!=&keep[i] ||
        candidates[i].inst!=&inst[i] || candidates[i].seeds!=&seeds[i]) return 7;
  }
  printf("recorded cursor: %u observations, exact candidate identity and navigation\n",cases);
  return 0;
}
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tcalc',type=Path,default=ROOT.parent/'tcalc')
    parser.add_argument('--admit',action='store_true')
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=parser.parse_args();tcalc=args.tcalc.resolve()
    source=tcalc/'cesta/tc_recorded_cursor.cesta'
    work=Path(tempfile.mkdtemp(prefix='tcalc-recorded-cursor-',dir=ROOT/'build'))
    host=work/'host.c';host.write_text(HOST)
    routes=[];plans=[]
    for cc in ('gcc','clang'):
        exported=work/(cc+'.c');plan=work/(cc+'.plan')
        run([BINARY,'export-c',source,'--cc',cc,'-o',exported,'--plan',plan,'--','-I'+str(tcalc)])
        plans.append(plan)
        for opt in ('-O0','-O2','-O1'):
            san=opt=='-O1'
            flags=['-std=c11',opt,'-Wall','-Wextra','-Werror','-g0','-I'+str(tcalc)]
            if san:flags+=['-fsanitize=address,undefined','-fno-pie','-no-pie']
            binary=work/(cc+opt)
            run([cc,*flags,exported,host,'-o',binary])
            result=run([binary]);routes.append(result.stdout.decode().strip())
        flags=['-std=c11','-O2','-g0','-I'+str(tcalc)]
        direct=work/(cc+'-direct.o');plain=work/(cc+'-plain.o')
        run([BINARY,'cc','--cc',cc,*flags,'-c',source,'-o',direct])
        run([cc,*flags,'-c',exported,'-o',plain])
        for obj in (direct,plain):run(['objcopy','-O','binary','--only-section=.text',obj,obj.with_suffix('.text')])
        assert direct.with_suffix('.text').read_bytes()==plain.with_suffix('.text').read_bytes()
    assert len(set(routes))==1,routes
    document,=parse(source.read_bytes())
    unit,=[x for x in document[2:] if x[0]=='unit']
    for form in unit[1:]:
        if form[0]=='use-macros':form[1]=Text(str(ROOT/'macros/Records.module').encode())
    ownership,=[x for x in unit[1:] if x[0]=='ownership']
    negative=copy.deepcopy(document)
    nunit,=[x for x in negative[2:] if x[0]=='unit']
    own,=[x for x in nunit[1:] if x[0]=='ownership']
    spec,=parse(b'(function dangling (returns-borrow 0 nullable))')
    own.append(spec)
    nunit.append([Sym('native'),Text(b'''
const TcPreparedStep *dangling(const TcPreparedStep *anchor) {
  (void)anchor;
  TcPreparedStep local={0}; size_t position=0;
  return tc_cetta_cursor_advance(&local,&position,1);
}
''')])
    bad=work/'dangling.cesta';bad.write_text(dumps(negative))
    for cc in ('gcc','clang'):
        result=run([BINARY,'cc','--cc',cc,'-std=c11','-I'+str(tcalc),'-fsyntax-only',bad],accepted=False)
        assert b'returned borrow escapes its declared input' in result.stderr,result.stderr
    text=(work/'gcc.c').read_text()
    mutations={
        'no_advance':('++state->position;','(void)state->position;'),
        'double_advance':('++state->position;','state->position += 2;'),
        'lost_candidate':('return state->candidate;','return NULL;'),
        'lost_navigation':('*position = cursor.ctx.position;','*position = 0;'),
    }
    for name,(old,new) in mutations.items():
        assert text.count(old)==1,(name,old)
        mutant=work/(name+'.c');mutant.write_text(text.replace(old,new))
        binary=work/name
        run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(tcalc),mutant,host,'-o',binary])
        run([binary],accepted=False)
    if args.admit:
        from ownership_admission import admit_record
        for plan in plans:
            record,=[r for r in parse(plan.read_bytes()) if r[0]=='Ownership']
            admit_record(record,plan.with_suffix('.lean'),args.lean_root)
    summary=dict(routes=routes,mutations=list(mutations),dangling_refusals=2,
                 authored_exported_instruction_identity=True,admitted=args.admit,
                 boundary='host record-address selection and its array extent remain outside the checked provider')
    (work/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(f'tcalc recorded cursor: {routes[0]}; six GCC/Clang routes, instruction identity, '
          f'{len(mutations)} mutations, two dangling-caller refusals'
          +('; actual plans admitted' if args.admit else ''))


if __name__=='__main__':main()
