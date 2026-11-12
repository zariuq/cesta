"""Callable provenance, receiver access and retained context lifetimes."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from ownership_core import ROOT, BINARY, document

PREFIX = '''typedef struct { int value; } Context;
typedef const int *(*Read)(const Context *);
const int *first(const Context *c) { return &c->value; }
const int *second(const Context *c) { return &c->value; }
typedef struct { const Context *ctx; Read next; } Cursor;
const int *invoke(const Cursor *c) { return c->next(c->ctx); }
'''
CONTRACTS = '''(callback Read (targets first second))
(function first (returns-borrow 0 value))
(function second (returns-borrow 0 value))
(record Cursor (field ctx (borrow read)) (field next (callback Read)))
(function invoke (returns-borrow 0 ctx))'''

NARROW = PREFIX.split('typedef struct { const Context *ctx; Read next; } Cursor;')[0] + \
    'typedef const int *(*ReadFirst)(const Context *);\n'
NARROW_CONTRACTS = '''(callback Read (targets first second)) (callback ReadFirst (targets first))
(function first (returns-borrow 0 value))
(function second (returns-borrow 0 value))'''
OPTIONAL = PREFIX.replace('const int *invoke(const Cursor *c) { return c->next(c->ctx); }\n','')
OPTIONAL_CONTRACTS = '''(callback Read (targets first second))
(function first (returns-borrow 0 value))
(function second (returns-borrow 0 value))
(record Cursor (field ctx (borrow read)) (field next (callback Read nullable)))'''


def run(args, accepted=True):
    result = subprocess.run(list(map(str,args)),capture_output=True,text=True)
    assert (result.returncode == 0) == accepted, (args,result.returncode,result.stderr[-4000:])
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admit',action='store_true')
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=parser.parse_args()
    work=Path(tempfile.mkdtemp(prefix='ownership-callbacks-',dir=ROOT/'build'))
    factory='''Cursor capture(const Context *ctx) { return (Cursor){ctx,first}; }
'''
    factory_contract=' (function capture (returns-record ((ctx) (borrow 0)) ((next) static)))'
    positive={
        'cursor': (PREFIX+'''int main(void) { Context c={7}; Cursor cur={&c,first};
          int a=*invoke(&cur); cur.next=second; return a+*invoke(&cur)!=14; }''',CONTRACTS),
        'parameter': (PREFIX+'''const int *apply(Read next,const Context *c) { return next(c); }
          int main(void) { Context c={9}; return *apply(second,&c)!=9; }''',
          CONTRACTS+' (function apply (calls 0 Read) (returns-borrow 1 value))'),
        'local': (PREFIX+'''int main(void) { Context c={11}; Read fp=&first; return *fp(&c)!=11; }''',CONTRACTS),
        'retained_context': (PREFIX+factory+'''int main(void) { Context c={13};
          Cursor cur=capture(&c); return *invoke(&cur)!=13; }''',CONTRACTS+factory_contract),
        'mutable_receiver': ('''typedef struct {int value;} Context;
          typedef int (*Step)(Context *);
          int step(Context *c) { return ++c->value; }
          int apply(Step next,Context *c) { return next(c); }
          int main(void) { Context c={0}; return apply(step,&c)!=1; }''',
          '(callback Step (targets step)) (function apply (calls 0 Step))'),
        'consumed_receiver': ('''#include <stdlib.h>
          typedef struct {int value;} Context; typedef void(*Consume)(Context*);
          void consume(Context*c){free(c);}
          void apply(Consume next,Context*c){next(c);}
          int main(void){Context*c=malloc(sizeof(Context));if(!c){free(c);return 0;}
            c->value=7;apply(consume,c);return 0;}''',
          '(allocator heap malloc free (obligation "C allocation/release family.")) '
          '(callback Consume (targets consume)) (function consume (owns 0 heap)) '
          '(function apply (calls 0 Consume) (owns 1 heap))'),
        'stored_context': ('''typedef struct{int value;}Context;
          typedef void(*Save)(const Context*,const Context**);
          void save(const Context*c,const Context**out){*out=c;}
          void apply(Save next,const Context*c,const Context**out){next(c,out);}
          int main(void){Context c={7};const Context*out=0;apply(save,&c,&out);return out->value!=7;}''',
          '(callback Save (targets save)) (function save (stores 1 0)) '
          '(function apply (calls 0 Save) (stores 2 1))'),
        # A contract with fewer targets may flow into one that lists them all.
        'narrow_into_wide': (NARROW+'''const int *wide(Read next,const Context *c) { return next(c); }
          const int *relay(ReadFirst next,const Context *c) { return wide(next,c); }
          int main(void) { Context c={5}; return *relay(first,&c)!=5; }''',
          NARROW_CONTRACTS+' (function wide (calls 0 Read) (returns-borrow 1 value))'
          ' (function relay (calls 0 ReadFirst) (returns-borrow 1 value))'),
        # A nullable callback field may hold null; a guard admits the call.
        'nullable_field_guarded': (OPTIONAL+'''int peek(const Cursor *c) { if (c->next) return *c->next(c->ctx); return -1; }
          int main(void) { Context x={3}; Cursor a={&x,first}; Cursor b={&x,0};
            int r=peek(&a)+peek(&b); a.next=0; return r+peek(&a)!=1; }''',OPTIONAL_CONTRACTS),
    }
    negative={
        'different_contract':(PREFIX,CONTRACTS.replace('(function second (returns-borrow 0 value))','(function second (returns-static))')),
        'unknown_target':(PREFIX,CONTRACTS.replace('(targets first second)','(targets missing)')),
        'duplicate_target':(PREFIX,CONTRACTS.replace('(targets first second)','(targets first first)')),
        'empty_targets':(PREFIX,CONTRACTS.replace('(targets first second)','(targets)')),
        'unlisted_target':(PREFIX+'const int *third(const Context*c){return &c->value;} int main(void){Context c={0};Cursor cur={&c,third};return *invoke(&cur);}',CONTRACTS+' (function third (returns-borrow 0 value))'),
        'null_callback':(PREFIX+'int main(void){Context c={0};Cursor cur={&c,0};return *invoke(&cur);}',CONTRACTS),
        'wrong_prototype':(PREFIX+'int other(Context*c){return c->value;}',CONTRACTS.replace('(targets first second)','(targets first other)')),
        'unknown_parameter':(PREFIX+'const int *apply(Read next,const Context*c){return next(c);}',CONTRACTS+' (function apply (returns-borrow 1 value))'),
        'dangling_context':(PREFIX+factory+'Cursor bad(void){Context c={0};return capture(&c);}',CONTRACTS+factory_contract+' (function bad (returns-record ((ctx) static) ((next) static)))'),
        'borrowed_result_write':(PREFIX+'int main(void){Context c={0};Cursor cur={&c,first};const int*p=invoke(&cur);c.value=2;return *p;}',CONTRACTS),
        'context_read_then_mutate':('''typedef struct{int value;}Context;typedef int(*Step)(Context*);
          int step(Context*c){return ++c->value;} int apply(Step f,Context*c){return f(c);}
          int main(void){Context c={0};const int*p=&c.value;return apply(step,&c)+*p;}''',
          '(callback Step (targets step)) (function apply (calls 0 Step))'),
        'owned_global':('''static int value;typedef int(*Step)(void);int step(void){return ++value;}
          int apply(Step f){return f();}''', '(global value step) (callback Step (targets step)) (function apply (calls 0 Step))'),
        'callee_changes_during_arguments':('''typedef int(*Next)(int);int first(int n){return n;}
          int replace(Next*out){*out=first;return 1;}
          int main(void){Next f=first;return f(replace(&f));}''',
          '(function replace (stores 0 static))'),
        'wide_into_narrow':(NARROW+'''const int *narrow(ReadFirst next,const Context *c) { return next(c); }
          const int *relay(Read next,const Context *c) { return narrow(next,c); }''',
          NARROW_CONTRACTS+' (function narrow (calls 0 ReadFirst) (returns-borrow 1 value))'
          ' (function relay (calls 0 Read) (returns-borrow 1 value))'),
        'nullable_field_unguarded':(OPTIONAL+'int peek(const Cursor *c) { return *c->next(c->ctx); }',OPTIONAL_CONTRACTS),
        'nullable_field_local_unguarded':(OPTIONAL+'int main(void) { Context x={3}; Cursor b={&x,0}; return *b.next(b.ctx); }',OPTIONAL_CONTRACTS),
        'nullable_field_wrong_guard':(OPTIONAL+'int peek(const Cursor *c) { if (!c->next) return *c->next(c->ctx); return -1; }',OPTIONAL_CONTRACTS),
        'cast_function_type':('''int first(int n){return n;} typedef int(*Wrong)(void);
          int main(void){Wrong f=(Wrong)first;return f();}''',''),
    }
    plans=[]
    for name,(body,contracts) in positive.items():
        source=work/(name+'.cesta');source.write_text(document(body,contracts))
        exported=work/(name+'.c');plan=work/(name+'.plan')
        run([BINARY,'export-c',source,'-o',exported,'--plan',plan])
        plans.append(plan)
        reference=work/(name+'-reference.c');reference.write_text(body)
        for cc in ('gcc','clang'):
            flags=['-std=c11','-O2','-g0','-Wall','-Wextra','-Werror']
            for variant,unit in [('export',exported),('reference',reference)]:
                obj=work/f'{name}-{cc}-{variant}.o'
                run([cc,*flags,'-c',unit,'-o',obj])
                run(['objcopy','-O','binary','--only-section=.text',obj,obj.with_suffix('.text')])
            assert (work/f'{name}-{cc}-export.text').read_bytes()==(work/f'{name}-{cc}-reference.text').read_bytes()
            for san in (False,True):
                binary=work/f'{name}-{cc}-{san}'
                run([cc,*flags,*(['-fsanitize=address,undefined','-fno-pie','-no-pie'] if san else []),exported,'-o',binary])
                run([binary])
    for name,(body,contracts) in negative.items():
        source=work/(name+'.cesta');source.write_text(document(body,contracts))
        for cc in ('gcc','clang'):
            result=run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only',source],accepted=False)
            assert 'ownership' in result.stderr,(name,result.stderr)
    if args.admit:
        from ownership_admission import admit_record
        from oracles.pro_compact_cesta import parse
        for plan in plans:
            record,=parse(plan.read_bytes())
            admit_record(record,plan.with_suffix('.lean'),args.lean_root)
    print(f'callbacks: {len(positive)} executable contracts, {len(negative)} source refusals; GCC/Clang, sanitizers, exact exported instructions'+('; emitted plans admitted' if args.admit else ''),flush=True)


if __name__=='__main__':main()
