"""Nested output records retain original input-field lifetimes."""

import argparse
import json
from pathlib import Path
import tempfile

from checked_spans import BINARY, ROOT, run

PREFIX = '''typedef struct {const int *item;} Payload;
typedef struct {Payload firing; const int *profile;} Event;
'''
CONTRACTS = '''(record Payload (field item (borrow read nullable)))
 (record Event (field profile (borrow read nullable)))
 (function produce (stores-record 2
   ((firing item) (borrow 0 item)) ((profile) (borrow 1))))'''
PRODUCE = '''void produce(const Payload *input,const int *profile,Event *out) {
  out->firing.item=input->item;
  out->profile=profile;
}
'''
MAIN = '''int main(void) {
  int a=13,b=29;Event event={{0},0};
  {Payload input={&a};produce(&input,&b,&event);}
  if(!event.firing.item || !event.profile)return 1;
  return *event.firing.item+*event.profile==42?0:1;
}
'''


def document(code, contracts=CONTRACTS):
    return '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) '+contracts+') (native '+json.dumps(code)+')))'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--admit',action='store_true')
    p.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=p.parse_args()
    work=Path(tempfile.mkdtemp(prefix='record-results-',dir=ROOT/'build'))
    source=work/'event.cesta';source.write_text(document(PREFIX+PRODUCE+MAIN))
    for cc in ('gcc','clang'):
        (work/cc).mkdir()
        exported,plan=work/cc/'event.c',work/f'{cc}.plan'
        run([BINARY,'export-c',source,'--std=c11','--cc',cc,'-o',exported,'--plan',plan])
        for sanitized in (False,True):
            output=work/f'{cc}-{sanitized}'
            flags=['-std=c11','-O1','-g0','-Wall','-Wextra','-Werror']
            if sanitized:flags+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
            run([cc,*flags,exported,'-o',output]);run([output])
            if not sanitized:
                authored=work/f'{cc}-authored'
                run([BINARY,'cc','--cc',cc,*flags,source,'-o',authored]);run([authored])
                assert authored.read_bytes()==output.read_bytes(), (cc,'record-output export identity')
    negatives={
      'local_field':document(PREFIX+PRODUCE.replace('out->firing.item=input->item;',
                        'int local=7;out->firing.item=&local;')),
      'partial_field':document(PREFIX+PRODUCE.replace('out->profile=profile;','if(*profile)out->profile=profile;')),
      'wrong_origin':document(PREFIX+PRODUCE.replace('out->firing.item=input->item;','out->firing.item=profile;')),
      'short_contract':document(PREFIX+PRODUCE,CONTRACTS.replace(' ((profile) (borrow 1))','')),
      'wrong_path':document(PREFIX+PRODUCE,CONTRACTS.replace('(firing item)','(firing missing)')),
      'wrong_source_path':document(PREFIX+PRODUCE,CONTRACTS.replace('(borrow 0 item)','(borrow 0 missing)')),
      'duplicate_field':document(PREFIX+PRODUCE,CONTRACTS.replace('((profile) (borrow 1))','((firing item) (borrow 1))')),
      'dangling_caller':document(PREFIX+PRODUCE+'''int main(void){
        Event e={{0},0};int b=2;
        {int a=7;Payload input={&a};produce(&input,&b,&e);}
        return e.firing.item?*e.firing.item:0;
      }'''),
    }
    for name,text in negatives.items():
        negative=work/f'{name}.cesta';negative.write_text(text)
        run([BINARY,'cc','--cc','gcc','-std=c11','-fsyntax-only',negative],accepted=False)
    # Repeated fields in an inline array must be distinguished by their offsets.
    array_source=work/'array.cesta'
    array_source.write_text(document('''typedef struct {const int *item;} Payload;
typedef struct {Payload rows[2];} Event;
void produce(const int *a,const int *b,Event *out){out->rows[0].item=a;out->rows[1].item=b;}
int main(void){int a=3,b=5;Event e={{{0},{0}}};produce(&a,&b,&e);if(!e.rows[0].item||!e.rows[1].item)return 1;return *e.rows[0].item+*e.rows[1].item==8?0:1;}
''','''(record Payload (field item (borrow read nullable))) (record Event)
(function produce (stores-record 2 ((rows 0 item) (borrow 0)) ((rows 1 item) (borrow 1))))'''))
    array_plan=work/'array.plan'
    run([BINARY,'export-c',array_source,'--std=c11','-o',work/'array.c','--plan',array_plan])
    run(['gcc','-std=c11',work/'array.c','-o',work/'array']);run([work/'array'])
    out_of_range=work/'array-out-of-range.cesta'
    out_of_range.write_text(array_source.read_text().replace('(rows 1 item)','(rows 2 item)'))
    run([BINARY,'cc','--cc','gcc','-std=c11','-fsyntax-only',out_of_range],accepted=False)
    source_array=work/'array-origin.cesta'
    source_array.write_text(document(PREFIX+'typedef struct {Payload rows[2];} Input;\n'
        +PRODUCE.replace('const Payload *input','const Input *input').replace('input->item','input->rows[1].item')
        +MAIN.replace('Payload input={&a}', 'Input input={{{&b},{&a}}}'),
        CONTRACTS.replace('(borrow 0 item)','(borrow 0 rows 1 item)')+' (record Input)'))
    run([BINARY,'export-c',source_array,'--std=c11','-o',work/'array-origin.c','--plan',work/'array-origin.plan'])
    run(['gcc','-std=c11',work/'array-origin.c','-o',work/'array-origin']);run([work/'array-origin'])
    if args.admit:
        import ownership_admission
        for name in ('gcc','array','array-origin'):
            record,=ownership_admission.parse((work/f'{name}.plan').read_bytes())
            ownership_admission.admit_record(record,work/f'{name.title().replace("-", "")}Record.lean',args.lean_root)
    print(f'nested record outputs: GCC/Clang, sanitizers, array offsets, {len(negatives)+1} source refusals'
          + ('; actual plans kernel-admitted' if args.admit else ''))


if __name__=='__main__':main()
