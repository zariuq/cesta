"""Reusable declaration families, member ownership and allocator policy."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from macro_engine import ROOT, BINARY


def run(args, accepted=True):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True)
    assert (p.returncode==0)==accepted,(args,p.returncode,p.stderr[-5000:])
    return p


def document(module,theory,forms,host=''):
    return '(cesta native-c/1 compact-c/2 (unit (include <stddef.h>) (include <stdlib.h>) '+\
        '(use-macros '+json.dumps(str(module))+' '+theory+') '+forms+\
        (' (native '+json.dumps(host)+')' if host else '')+'))'


FLAGS=['-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror','-g0']
OWNER='record member receiver has its declared owner'
CURSOR_TYPES="""typedef struct { int value; } Context;
typedef const int *(*Read)(const Context *);
static const int *first(const Context *c) { return &c->value; }
static const int *second(const Context *c) { return &c->value; }
"""
CURSOR_HOST="""int main(void) { Context c={7}; IntCursor cur={&c,first};
  int a=*next_int(&cur); cur.next=second; return a+*next_int(&cur)!=14; }
"""
CURSOR_CONTRACTS="""(callback Read (targets first second))
(function first (returns-borrow 0 value))
(function second (returns-borrow 0 value))"""
CURSOR_BY_HAND="""typedef struct IntCursor { const Context *ctx; Read next; } IntCursor;
static inline const int *next_int(const IntCursor *self) { return self->next(self->ctx); }
"""


def unit(module,theory,forms,before='',after='',contracts=None):
    """A unit that declares native types, uses one family, then runs native code."""
    return '(cesta native-c/1 compact-c/2 (unit '+\
        ('(ownership (check-all) '+contracts+') ' if contracts is not None else '')+\
        '(use-macros '+json.dumps(str(module))+' '+theory+') '+\
        ('(native '+json.dumps(before)+') ' if before else '')+forms+\
        (' (native '+json.dumps(after)+')' if after else '')+'))'


def text_of(cc,source,flags):
    obj=source.with_name(source.stem+'-'+cc+'.o')
    run([cc,*flags,'-c',source,'-o',obj])
    run(['objcopy','-O','binary','--only-section=.text',obj,obj.with_suffix('.text')])
    return obj.with_suffix('.text').read_bytes()


def cursors(work,module):
    """The cursor family under callback and borrowed-context contracts."""
    form='(read-cursor int Context Read IntCursor next_int)'
    source=work/'cursor.cesta'
    source.write_text(unit(module,'Cursors',form,CURSOR_TYPES,CURSOR_HOST,CURSOR_CONTRACTS))
    exported=work/'cursor.c';plan=work/'cursor.plan'
    run([BINARY,'export-c',source,'-o',exported,'--plan',plan])
    assert exported.read_text().count(OWNER)==1
    by_hand=work/'cursor-by-hand.c';by_hand.write_text(CURSOR_TYPES+CURSOR_BY_HAND+CURSOR_HOST)
    for cc in ('gcc','clang'):
        assert text_of(cc,exported,[*FLAGS,'-O2'])==text_of(cc,by_hand,[*FLAGS,'-O2'])
        for extra in (['-O0'],['-O2'],['-O1','-fsanitize=address,undefined','-fno-pie','-no-pie']):
            binary=work/('cursor-'+cc+extra[0]+('-san' if len(extra)>1 else ''))
            run([cc,*FLAGS,*extra,exported,'-o',binary]);run([binary])
    third='static const int *third(const Context *c) { return &c->value; }\n'
    refusals={
        'generated_contract_wrong':(CURSOR_CONTRACTS+' (function next_int (returns-static))',
            CURSOR_TYPES,CURSOR_HOST,'duplicate function ownership contract'),
        'generated_contract_missing':(CURSOR_CONTRACTS,
            CURSOR_TYPES,CURSOR_HOST,'no declared lifetime origin'),
        'unlisted_target':(CURSOR_CONTRACTS+' (function third (returns-borrow 0 value))',CURSOR_TYPES+third,
            'int main(void){Context c={0};IntCursor cur={&c,third};return *next_int(&cur);}','admitted implementation'),
        'null_callback':(CURSOR_CONTRACTS,CURSOR_TYPES,
            'int main(void){Context c={0};IntCursor cur={&c,0};return *next_int(&cur);}','non-null field contract'),
        'result_outlives_write':(CURSOR_CONTRACTS,CURSOR_TYPES,
            'int main(void){Context c={0};IntCursor cur={&c,first};const int*p=next_int(&cur);c.value=2;return *p;}','overlapping loan'),
        'dangling_context':(CURSOR_CONTRACTS+' (function bad (returns-record ((ctx) static) ((next) static)))',CURSOR_TYPES,
            'IntCursor bad(void){Context c={0};IntCursor cur={&c,first};return cur;} int main(void){return 0;}','declared input lifetime'),
    }
    for name,(contracts,before,after,needle) in refusals.items():
        selected=module
        if name=='generated_contract_missing':
            selected=work/'missing-result.module'
            selected.write_text(module.read_text().replace(
                '(function (LocalRef "ordinary" run) ("returns-borrow" 0 (LocalRef "member" ctx)))',''))
        path=work/('cursor-'+name+'.cesta');path.write_text(unit(selected,'Cursors',form,before,after,contracts))
        for cc in ('gcc','clang'):
            result=run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only',path],accepted=False)
            assert 'ownership' in result.stderr and needle in result.stderr,(name,result.stderr)
    shapes={
        'colliding_exports':'(read-cursor int Context Read Same Same)',
        'wrong_name':'(read-cursor int Context Read (call foo) next_int)',
        'opaque_context':'(read-cursor int (native "Context") Read IntCursor next_int)',
    }
    for name,forms in shapes.items():
        path=work/('cursor-'+name+'.cesta');path.write_text(unit(module,'Cursors',forms,CURSOR_TYPES))
        run([BINARY,'export-c',path,'-o',work/('cursor-'+name+'.c')],accepted=False)
    return len(refusals),len(shapes),plan


def receivers(work):
    """A receiver whose type the caller supplies must be the member's own record."""
    module=ROOT/'tests/modules/RecordReceivers.module'
    run([BINARY,'check-macros',module,'Receivers'])
    other='typedef struct Other { int value; } Other;\n'
    host='int main(void){Box b={7};Slot s={9};return get_box(&b)+*slot_ref(&s)!=16;}\n'
    source=work/'receivers.cesta'
    source.write_text(unit(module,'Receivers','(peek int Box Box get_box) (peek-pointer int Slot Slot slot_ref)',after=host))
    exported=work/'receivers.c'
    run([BINARY,'export-c',source,'-o',exported])
    assert exported.read_text().count(OWNER)==2
    for cc in ('gcc','clang'):
        binary=work/('receivers-'+cc)
        run([cc,*FLAGS,'-O2',exported,'-o',binary]);run([binary])
    refused=0
    for name,forms in {
        'fn':'(peek int Other Box get_box)',
        'fn_shape':'(peek-pointer int Other Slot slot_ref)',
    }.items():
        path=work/('receiver-'+name+'.cesta');path.write_text(unit(module,'Receivers',forms,other,'int main(void){return 0;}\n'))
        exported=work/('receiver-'+name+'.c')
        run([BINARY,'export-c',path,'-o',exported])
        lines=exported.read_text().splitlines(keepends=True)
        unguarded=work/('receiver-'+name+'-unguarded.c')
        unguarded.write_text(''.join(line for line in lines if OWNER not in line))
        assert len(lines)-len(unguarded.read_text().splitlines())==1
        for cc in ('gcc','clang'):
            result=run([cc,*FLAGS,'-Wno-unused-function','-fsyntax-only',exported],accepted=False)
            assert OWNER in result.stderr,(name,cc,result.stderr)
            # Without the receiver check, C accepts any record with a member of that spelling.
            run([cc,*FLAGS,'-Wno-unused-function','-fsyntax-only',unguarded])
            refused+=1
    return refused


def state_cursors(work,module):
    types='''typedef struct { const int *item; unsigned left; } Context;
typedef const int *(*Step)(Context *);
static const int *first(Context *c) {
  if (!c->left) return 0;
  --c->left;
  return c->item;
}
'''
    contracts='''(record Context (field item (borrow read nullable)))
(callback Step (targets first))
(function first (returns-borrow 0 item nullable))'''
    forms='(state-cursor int Context Step item IntCursor next_int)'
    host='''int main(void) {
  int value=7; Context initial={&value,2};
  IntCursor cursor={initial,first};
  const int *a=next_int(&cursor);
  if (!a || *a!=7 || cursor.ctx.left!=1 || initial.left!=2) return 1;
  const int *b=next_int(&cursor);
  if (!b || *b!=7 || cursor.ctx.left!=0 || initial.left!=2) return 2;
  if (next_int(&cursor)) return 3;
  return 0;
}
'''
    source=work/'state-cursor.cesta'
    source.write_text(unit(module,'Cursors',forms,types,host,contracts))
    exported=work/'state-cursor.c';plan=work/'state-cursor.plan'
    run([BINARY,'export-c',source,'-o',exported,'--plan',plan])
    by_hand=work/'state-cursor-by-hand.c'
    by_hand.write_text(types+'''typedef struct IntCursor { Context ctx; Step next; } IntCursor;
static inline const int *next_int(IntCursor *self) { return self->next(&self->ctx); }
'''+host)
    for cc in ('gcc','clang'):
        assert text_of(cc,exported,[*FLAGS,'-O2'])==text_of(cc,by_hand,[*FLAGS,'-O2'])
        for extra in (['-O0'],['-O2'],['-O1','-fsanitize=address,undefined','-fno-pie','-no-pie']):
            binary=work/('state-cursor-'+cc+extra[0]+('-san' if len(extra)>1 else ''))
            run([cc,*FLAGS,*extra,exported,'-o',binary]);run([binary])
    # Identical explicit contracts remain compatible with family declarations.
    explicit=work/'state-cursor-explicit.cesta'
    explicit.write_text(unit(module,'Cursors',forms,types,host,contracts+'''
(record IntCursor (field next (callback Step)))
(function next_int (returns-borrow 0 ctx item nullable))'''))
    run([BINARY,'export-c',explicit,'-o',work/'state-cursor-explicit.c'])
    cases={
        'dangling_state': ('const int *bad(void) { int x=0; Context c={&x,1}; '
          'IntCursor cursor={c,first}; return next_int(&cursor); }',
          contracts+' (function bad (returns-static nullable))','static lifetime'),
        'mutated_borrow': ('int main(void) { int x=0; Context c={&x,1}; '
          'IntCursor cursor={c,first}; const int *p=next_int(&cursor); x=2; return p?*p:0; }',
          contracts,'overlapping loan'),
        'wrong_callback': ('static const int *other(Context*c){return c->item;} '
          'int main(void){int x=0;Context c={&x,1};IntCursor cursor={c,other};return next_int(&cursor)!=0;}',
          contracts+' (function other (returns-borrow 0 item nullable))','admitted implementation'),
        'null_callback': ('int main(void){int x=0;Context c={&x,1};IntCursor cursor={c,0};return next_int(&cursor)!=0;}',
          contracts,'non-null field contract'),
    }
    for name,(body,spec,needle) in cases.items():
        path=work/('state-'+name+'.cesta');path.write_text(unit(module,'Cursors',forms,types,body,spec))
        result=run([BINARY,'cc','--cc','gcc','-std=c11','-fsyntax-only',path],accepted=False)
        assert needle in result.stderr,(name,result.stderr)
    invalid={
        'unbound_identity':module.read_text().replace('(NameArgument origin)','(NameArgument absent)'),
        'unbound_local':module.read_text().replace('(LocalRef "member" ctx) (NameArgument origin)',
          '(LocalRef "member" absent) (NameArgument origin)'),
        'authority_injection':module.read_text().replace('(Contracts\n', '(Contracts\n (unchecked hidden)\n',1),
    }
    for name,text in invalid.items():
        path=work/(name+'.module');path.write_text(text)
        run([BINARY,'check-macros',path,'Cursors'],accepted=False)
    path=work/'compound-name.cesta'
    path.write_text(unit(module,'Cursors',forms.replace('Step item','(const Step) item'),types,host,contracts))
    result=run([BINARY,'export-c',path,'-o',work/'compound-name.c'],accepted=False)
    assert 'requires one C identifier' in result.stderr,result.stderr
    # The contract's nominal name requirement belongs only to the optional check.
    plain=work/'compound-name-plain.cesta'
    plain.write_text(unit(module,'Cursors',forms.replace('Step item','(const Step) item'),types,host))
    exported_plain=work/'compound-name-plain.c'
    run([BINARY,'export-c',plain,'-o',exported_plain])
    for cc in ('gcc','clang'):
        binary=work/('compound-name-plain-'+cc)
        run([cc,*FLAGS,'-O2',exported_plain,'-o',binary]);run([binary])
    return plan,len(cases)+len(invalid)+1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admit',action='store_true')
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=parser.parse_args()
    work=Path(tempfile.mkdtemp(prefix='macro-records-',dir=ROOT/'build'))
    module=ROOT/'macros/Records.module'
    host='''
static unsigned allocations, fail_next;
static void *policy(void *old, size_t bytes) {
  ++allocations;
  if (fail_next) { fail_next=0; return NULL; }
  return realloc(old,bytes);
}
int main(void) {
  Ints ints={0}; Words words={0};
  fail_next=1;
  if (push_int(&ints,7,policy) || ints.data || ints.len || ints.cap) return 1;
  for (int i=0;i<1024;++i) if (!push_int(&ints,i,policy)) return 2;
  for (int i=0;i<1024;++i) if (ints.data[i]!=i) return 3;
  size_t cap=ints.cap; int *saved=ints.data;
  fail_next=1;
  if (push_int(&ints,99,policy) || ints.data!=saved || ints.cap!=cap || ints.len!=1024) return 4;
  for (int i=0;i<1024;++i) if (ints.data[i]!=i) return 5;
  if (!push_word(&words,123u,policy) || words.data[0]!=123u) return 6;
  /* A caller-provided arena with spare capacity never needs a resize hook. */
  int arena[2]={4,0}; Ints fixed={arena,1,2};
  if (!push_int(&fixed,8,NULL) || arena[1]!=8 || push_int(&fixed,9,NULL)) return 7;
  /* Overflow is rejected before allocation or inspecting the claimed storage. */
  Ints huge={saved,(size_t)-1,(size_t)-1}; unsigned before=allocations;
  if (push_int(&huge,1,policy) || allocations!=before || huge.len!=(size_t)-1) return 8;
  Ints bad={NULL,0,1};
  if (push_int(&bad,1,policy) || bad.data || bad.len || bad.cap!=1) return 9;
  free(ints.data);free(words.data);return 0;
}
'''
    source=work/'worklists.cesta'
    source.write_text(document(module,'Worklists','(worklist int size_t Ints push_int) (worklist unsigned size_t Words push_word)',host))
    exported=work/'worklists.c';plan=work/'worklists.plan'
    run([BINARY,'export-c',source,'-o',exported,'--plan',plan])
    assert '(position declarations)' in plan.read_text()
    for cc in ('gcc','clang'):
        flags=['-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror','-g0']
        for opt in ('-O0','-O2'):
            direct=work/f'{cc}-{opt}-direct.o';plain=work/f'{cc}-{opt}-plain.o'
            run([BINARY,'cc','--cc',cc,*flags,opt,'-c',source,'-o',direct])
            run([cc,*flags,opt,'-c',exported,'-o',plain])
            for obj in (direct,plain):run(['objcopy','-O','binary','--only-section=.text',obj,obj.with_suffix('.text')])
            assert direct.with_suffix('.text').read_bytes()==plain.with_suffix('.text').read_bytes()
            binary=work/f'{cc}-{opt}'
            run([cc,*flags,opt,exported,'-o',binary]);run([binary])
        binary=work/f'{cc}-san'
        run([cc,*flags,'-O1','-fsanitize=address,undefined','-fno-pie','-no-pie',exported,'-o',binary]);run([binary])
    box=ROOT/'tests/modules/RecordFamilies.module'
    run([BINARY,'check-macros',box,'Values'])
    text=box.read_text()
    mutations={
        'absent_owner':text.replace('(Local value "member" tag)','(Local value "member" missing)'),
        'wrong_owner':text.replace('(Local value "member" tag)','(Local value "member" other) (Local other "tag")'),
        'missing_member':text.replace('(List (TypeArgument element) (LocalRef "member" value))','(List (TypeArgument element) unrelated)'),
        'value_argument':text.replace('(Arg element (CType void) "syntax" (Evaluation "syntax"))','(Arg element (CType int) "value" (Evaluation "once"))'),
        'bad_export':text.replace('(Export name)','(Export element)'),
        'opaque_body':text.replace('(Template (unit','(Template (unit (native "int hidden;")'),
    }
    for name,mutant in mutations.items():
        path=work/(name+'.module');path.write_text(mutant)
        run([BINARY,'check-macros',path,'Values'],accepted=False)
    for name,forms in {
        'colliding_exports':'(value-box int Collision Collision)',
        'wrong_name':'(value-box int (call foo) getter)',
        'wrong_element':'(value-box (native "int") Box getter)',
    }.items():
        path=work/(name+'.cesta');path.write_text(document(box,'Values',forms))
        run([BINARY,'export-c',path,'-o',work/(name+'.c')],accepted=False)
    checked,shaped,cursor_plan=cursors(work,module)
    state_plan,state_refused=state_cursors(work,module)
    if args.admit:
        # The generated cursor body is admitted like written code.
        from ownership_admission import admit_record
        from oracles.pro_compact_cesta import parse
        for plan in (cursor_plan,state_plan):
            ownership=[record for record in parse(plan.read_bytes()) if str(record[0])=='Ownership']
            assert len(ownership)==1
            admit_record(ownership[0],plan.with_suffix('.lean'),args.lean_root)
    guarded=receivers(work)
    print(f'record families: two typed worklists, explicit allocator/arena policies, refusal preservation, {len(mutations)+3} source refusals; '
          f'checked cursor with {checked} ownership refusals and {shaped} source refusals, hand-written instruction identity; '
          f'{guarded} foreign-receiver compilations refused; GCC/Clang O0/O2 and sanitizers'
          f'; family-supplied contracts, owned cursor state and {state_refused} further refusals'
          +('; cursor plan admitted' if args.admit else ''),flush=True)


if __name__=='__main__':main()
