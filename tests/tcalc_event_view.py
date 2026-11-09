"""Qualify the live tcalc event-view unit, including a dangling caller control."""

import argparse
import copy
import os
import subprocess
import tempfile
from pathlib import Path

from checked_spans import BINARY, ROOT, run
from oracles.pro_compact_cesta import Sym, Text, dumps, parse


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tcalc',type=Path,default=ROOT.parent/'tcalc')
    parser.add_argument('--admit',action='store_true')
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=parser.parse_args();tcalc=args.tcalc.resolve()
    source=tcalc/'cesta/tc_event_view.cesta'
    work=Path(tempfile.mkdtemp(prefix='tcalc-event-view-',dir=ROOT/'build'))
    original=(tcalc/'bridge/tc_cetta_bridge.c').read_text()
    begin=original.index('TcPreparedStep tc_event_firing(')
    end=original.index('\n}\n',begin)+3
    original=original[begin:end]
    document,=parse(source.read_bytes())
    unit,=[x for x in document[2:] if x[0]=='unit']
    native,=[x for x in unit[1:] if x[0]=='native']
    assert original.strip() in native[1].data.decode(),'Cesta event view has drifted from the ordinary-C reference'
    reference=work/'reference.c'
    reference.write_text('#include "bridge/tc_cetta_bridge.h"\n'+original.replace('tc_event_firing','reference_event_firing'))
    host=work/'host.c'
    host.write_text('''#include "bridge/tc_cetta_bridge.h"
TcPreparedStep reference_event_firing(const TcAdmittedEvent *);
int main(void){
  TcPreparedStep zero=tc_event_firing(NULL);
  if(zero.take||zero.keep||zero.inst||zero.seeds)return 1;
  for(unsigned mask=0;mask<16;mask++){
    TcAdmittedEvent event={0};event.firing.rule=17;event.firing.dest=23;
    event.ntake=(mask&1)!=0;event.nkeep=(mask&2)!=0;event.has_inst=(mask&4)!=0;
    event.firing.nseeds=(mask&8)!=0;
    event.owned_take[0]=0x80000001u;event.owned_keep[0]=0xfedcba98u;
    event.owned_inst[0]=0xfffffffeu;event.owned_seeds[0]=0xffffffffu;
    TcPreparedStep actual=tc_event_firing(&event),reference=reference_event_firing(&event);
    if(actual.rule!=reference.rule||actual.dest!=reference.dest||actual.nseeds!=reference.nseeds||
       actual.take!=reference.take||actual.keep!=reference.keep||actual.inst!=reference.inst||
       actual.seeds!=reference.seeds)return 2;
    if((actual.take&&*actual.take!=0x80000001u)||(actual.keep&&*actual.keep!=0xfedcba98u)||
       (actual.inst&&*actual.inst!=0xfffffffeu)||(actual.seeds&&*actual.seeds!=0xffffffffu))return 3;
    event.firing=actual;TcAdmittedEvent moved=event;
    actual=tc_event_firing(&moved);
    if((actual.take&&actual.take!=moved.owned_take)||(actual.keep&&actual.keep!=moved.owned_keep)||
       (actual.inst&&actual.inst!=moved.owned_inst)||(actual.seeds&&actual.seeds!=moved.owned_seeds))return 4;
  }
  return 0;
}
''')
    escaped='''TcPreparedStep escaped(const TcAdmittedEvent *anchor){
  (void)anchor;
  TcAdmittedEvent local={0};
  local.nkeep=1;
  local.owned_keep[0]=13;
  return tc_event_firing(&local);
}
'''
    negative=copy.deepcopy(document)
    nunit,=[x for x in negative[2:] if x[0]=='unit']
    ownership,=[x for x in nunit[1:] if x[0]=='ownership']
    contracts,=parse(b'(function escaped (returns-record ((take) (borrow 0)) ((keep) (borrow 0)) ((inst) (borrow 0)) ((seeds) (borrow 0))))')
    ownership.append(contracts)
    nunit.append([Sym('native'),Text(escaped.encode())])
    bad_source=work/'dangling.cesta';bad_source.write_text(dumps(negative))
    result=run([BINARY,'cc','--cc','gcc','-std=c11','-I',tcalc,'-fsyntax-only',bad_source],accepted=False)
    assert b'escapes its declared input lifetime' in result.stderr,result.stderr
    raw_bad=work/'dangling.c'
    raw_bad.write_text('#include "bridge/tc_cetta_bridge.h"\n__attribute__((noinline))\n'+escaped+'''
int main(void){TcAdmittedEvent anchor={0};TcPreparedStep view=escaped(&anchor);
  return view.keep?(int)view.keep[0]:0;}
''')
    for cc in ('gcc','clang'):
        output=work/cc;output.mkdir()
        exported=output/'event.c';plan=output/'event.plan'
        run([BINARY,'export-c',source,'--cc',cc,'--plan',plan,'-o',exported,'--','-I'+str(tcalc)])
        flags=['-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-I',tcalc]
        sanitizer=['-fsanitize=address,undefined','-fsanitize-address-use-after-scope','-fno-pie','-no-pie']
        for label,unit_source in (('export',exported),('reference',reference)):
            obj=output/(label+'.o')
            run([cc,*flags,'-g0','-c',unit_source,'-o',obj])
            run(['objcopy','-O','binary','--only-section=.text',obj,output/(label+'.text')])
        assert (output/'export.text').read_bytes()==(output/'reference.text').read_bytes(),(cc,'event-view instruction identity')
        for san in (False,True):
            binary=output/f'view-{san}'
            run([cc,*flags,*(sanitizer if san else []),exported,reference,host,'-o',binary]);run([binary])
        bad=output/'dangling'
        run([cc,*flags,*sanitizer,exported,raw_bad,'-o',bad])
        result=subprocess.run([bad],capture_output=True,env={**os.environ,
            'ASAN_OPTIONS':'detect_stack_use_after_return=1:detect_leaks=1:abort_on_error=0'})
        assert result.returncode!=0 and (b'stack-use-after-return' in result.stderr or b'stack-use-after-scope' in result.stderr),result.stderr
        (output/'dangling-sanitizer.log').write_bytes(result.stderr)
    if args.admit:
        import ownership_admission
        record,=parse((work/'gcc/event.plan').read_bytes())
        ownership_admission.admit_record(record,work/'EventView.lean',args.lean_root)
    print('tcalc event view: real public header and reference body; GCC/Clang instruction identity; 16 masks and relocated events; '
          'dangling caller refused by Cesta and caught by both sanitizers'+('; actual plan admitted' if args.admit else ''))


if __name__=='__main__':main()
