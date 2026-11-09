"""By-value returned records: original lifetimes and independent plan checks."""

import argparse
import json
from pathlib import Path
import tempfile

from checked_spans import BINARY, ROOT, run

PREFIX = '''typedef struct {const int *item;} Payload;
typedef struct {Payload firing;const int *profile;} Event;
'''
RECORDS = '''(record Payload (field item (borrow read nullable)))
 (record Event (field profile (borrow read nullable)))'''
RESULT = '(returns-record ((firing item) (borrow 0 item)) ((profile) (borrow 1)))'
MAKE = '''Event produce(const Payload *input,const int *profile) {
  Event result={{input->item},profile};return result;
}
Event forward(const Payload *input,const int *profile){return produce(input,profile);}
'''
MAIN = '''int main(void){
  int a=13,b=29;Payload input={&a};
  Event e=forward(&input,&b);
  if(!e.firing.item||!e.profile)return 1;
  if(*e.firing.item+*e.profile!=42)return 2;
  e=produce(&input,&b);
  if(!e.firing.item||!e.profile)return 3;
  return *e.firing.item+*e.profile==42?0:4;
}
'''


def document(code, contracts=None):
    if contracts is None: contracts=RECORDS+' (function produce '+RESULT+') (function forward '+RESULT+')'
    return '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) '+contracts+') (native '+json.dumps(code)+')))'


def main():
    p=argparse.ArgumentParser();p.add_argument('--admit',action='store_true')
    p.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=p.parse_args()
    work=Path(tempfile.mkdtemp(prefix='record-values-',dir=ROOT/'build'))
    positives={
        'values': document(PREFIX+MAKE+MAIN),
        'compound':document(PREFIX+MAKE.replace('Event result={{input->item},profile};return result;',
                                'return (Event){{input->item},profile};')+MAIN),
        'nullable_choice':document(PREFIX+MAKE.replace('Event result={{input->item},profile};',
                                'Event result={{profile?input->item:0},input->item?profile:0};')+MAIN),
        'nested_zero':document(PREFIX+MAKE+MAIN.replace('Event e=forward(&input,&b);',
                                'Event e={0};e=forward(&input,&b);')),
        'source_holder_ends':document(PREFIX+MAKE+'''int main(void){int a=13,b=29;Event e={{0},0};
          {Payload input={&a};e=produce(&input,&b);}
          if(!e.firing.item||!e.profile)return 1;
          return *e.firing.item+*e.profile==42?0:2;}'''),
    }
    owning_prefix='''#include <stddef.h>
extern void *record_allocate(size_t);
extern void record_release(void *);
typedef struct {int *item;} Box;
'''
    owning_contracts='''(allocator heap record_allocate record_release
      (obligation "Fresh initialized storage of the requested extent and matching release, including null."))
      (record Box (field item (owns heap nullable)))
      (function make (returns-record ((item) (owns heap))))
      (function relay (returns-record ((item) (owns heap))))'''
    owning_make='''Box make(void){int *p=(int *)record_allocate(sizeof(int));
      if(!p){record_release(p);return (Box){0};}*p=41;Box result={p};return result;}
      Box relay(Box value){return value;}
'''
    owning_main='''int main(void){Box first=make();Box second=relay(first);
      int value=0;if(second.item)value=*second.item;
      record_release(second.item);return value==41?0:1;}
'''
    positives['owned']=document(owning_prefix+owning_make+owning_main,owning_contracts)
    positives['owned_failure']=document(owning_prefix+owning_make+
        owning_main.replace('value==41','value==0'),owning_contracts)
    array_prefix='''typedef struct {const int *item;} Cell;
      typedef struct {Cell rows[2];} Table;
      Table assemble(const int *a,const int *b){Table result={{{a},{b}}};return result;}
      int main(void){int a=13,b=29;Table table=assemble(&a,&b);
        return *table.rows[0].item+*table.rows[1].item==42?0:1;}
    '''
    array_contracts='''(record Cell (field item (borrow read))) (record Table)
      (function assemble (returns-record ((rows 0 item) (borrow 0)) ((rows 1 item) (borrow 1))))'''
    positives['array']=document(array_prefix,array_contracts)
    pair_code=owning_prefix+'''typedef struct {Box rows[2];} Pair;
      Pair pack(Box a,Box b){Pair result={{a,b}};return result;}
      int main(void){Box a=make(),b=make();Pair result=pack(a,b);
        int total=0;if(result.rows[0].item)total+=*result.rows[0].item;
        if(result.rows[1].item)total+=*result.rows[1].item;
        record_release(result.rows[0].item);record_release(result.rows[1].item);
        return total==82?0:1;}
    '''
    pair_code=pair_code.replace('typedef struct {Box rows[2];} Pair;',owning_make+'typedef struct {Box rows[2];} Pair;')
    pair_contracts=owning_contracts+''' (record Pair)
      (function pack (returns-record ((rows 0 item) (owns heap)) ((rows 1 item) (owns heap))))'''
    positives['owned_pair']=document(pair_code,pair_contracts)
    provider=work/'allocator.c'
    provider.write_text('#include <stdlib.h>\nvoid *record_allocate(size_t n){return malloc(n);}\n'
                        'void record_release(void *p){free(p);}\n')
    failing_provider=work/'allocator_failure.c'
    failing_provider.write_text('#include <stdlib.h>\nvoid *record_allocate(size_t n){(void)n;return NULL;}\n'
                               'void record_release(void *p){free(p);}\n')
    for name,text in positives.items():
        source=work/f'{name}.cesta';source.write_text(text)
        for cc in ('gcc','clang'):
            output=work/cc;output.mkdir(exist_ok=True)
            exported,plan=output/f'{name}.c',output/f'{name}.plan'
            run([BINARY,'export-c',source,'--std=c11','--cc',cc,'-o',exported,'--plan',plan])
            for san in (False,True):
                binary=output/f'{name}-{san}'
                flags=['-std=c11','-O1','-g0','-Wall','-Wextra','-Werror']
                if san:flags+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
                extras=([failing_provider] if name=='owned_failure' else [provider]) if name.startswith('owned') else []
                run([cc,*flags,exported,*extras,'-o',binary]);run([binary])
                if not san:
                    authored=output/f'{name}-authored'
                    run([BINARY,'cc','--cc',cc,*flags,source,*extras,'-o',authored]);run([authored])
                    assert authored.read_bytes()==binary.read_bytes(),(cc,name,'export identity')
        if args.admit:
            import ownership_admission
            record,=ownership_admission.parse((work/'gcc'/f'{name}.plan').read_bytes())
            ownership_admission.admit_record(record,work/(name.title().replace('_','')+'.lean'),args.lean_root)
    negatives={
        'stack_escape':document(PREFIX+MAKE.replace('Event result={{input->item},profile};',
                            'int local=7;Event result={{&local},profile};')),
        'wrong_input':document(PREFIX+MAKE.replace('Event result={{input->item},profile};','Event result={{profile},profile};')),
        'missing_field':document(PREFIX+MAKE.replace('Event result={{input->item},profile};','Event result;result.profile=profile;')),
        'missing_contract':document(PREFIX+MAKE,RECORDS),
        'incomplete_contract':document(PREFIX+MAKE,RECORDS+' (function produce (returns-record ((profile) (borrow 1))))'),
        'caller_escape':document(PREFIX+MAKE+'''int main(void){int b=29;Event e={{0},0};
          {int a=13;Payload input={&a};e=produce(&input,&b);}
          return e.firing.item?*e.firing.item:0;}'''),
    }
    negatives['double_owned_move']=document(owning_prefix+owning_make+
        owning_main.replace('Box second=relay(first);','Box second=relay(first);Box third=relay(first);record_release(third.item);'),owning_contracts)
    negatives['owned_leak']=document(owning_prefix+owning_make+
        owning_main.replace('record_release(second.item);',''),owning_contracts)
    negatives['return_borrowed_owner']=document(owning_prefix+owning_make.replace(
        'Box result={p};return result;', 'Box result={p};const int *borrow=result.item;(void)borrow;return result;'),owning_contracts)
    negatives['double_array_move']=document(pair_code.replace('Pair result={{a,b}}','Pair result={{a,a}}'),pair_contracts)
    negatives['array_wrong_origin']=document(array_prefix.replace('result={{{a},{b}}}','result={{{b},{a}}}'),array_contracts)
    negatives['array_incomplete_contract']=document(array_prefix,array_contracts.replace(' ((rows 1 item) (borrow 1))',''))
    negatives['overwrite_live_owner']=document(owning_prefix+owning_make+
        owning_main.replace('Box second=relay(first);','Box second=relay(first);second=make();'),owning_contracts)
    negatives['conditional_stack_escape']=document(PREFIX+MAKE.replace('Event result={{input->item},profile};',
                                'int local=3;Event result={{profile?&local:0},profile};'))
    negatives['conditional_owner_copy']=document(owning_prefix+owning_make.replace(
        'Box result={p};', 'Box result={*p?p:0};'),owning_contracts)
    for name,text in negatives.items():
        source=work/f'bad-{name}.cesta';source.write_text(text)
        run([BINARY,'cc','--cc','gcc','-std=c11','-fsyntax-only',source],accepted=False)
    print(f'returned records: {len(positives)} programs, GCC/Clang, sanitizers, export identity; {len(negatives)} refusals'
          + ('; actual plans and mutations kernel-checked' if args.admit else ''))


if __name__=='__main__':main()
