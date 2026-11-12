"""Typed field contracts generate ordinary C visitors with explicit native policies."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent

HEADER = '''#ifndef RECORD_CONTROL_H
#define RECORD_CONTROL_H
#include <stdbool.h>
#include <stddef.h>
typedef struct Atom {int value;struct Atom *next;} Atom;
typedef struct Episode {unsigned identity;} Episode;
typedef struct TraceContext {Atom *source,*destination;bool copied[2];unsigned calls,fail_at;} TraceContext;
typedef struct Frame {Atom *left,*right;Atom **items;size_t len;Episode *episode;bool active;} Frame;
bool move_atom(TraceContext *,Atom **);
bool move_span(TraceContext *,Atom **,size_t);
#endif
'''

SCHEMA = '''(cesta record-visitors/1
 (owner atoms) (owner slots) (owner episode)
 (visitor atom (owner atoms) (type "Atom *") (call move_atom) (argument address)
   (effects write) (obligation "Copies shared/cyclic atoms and updates its borrowed slot; retains no frame/context."))
 (visitor span (owner slots) (type "Atom **") (call move_span) (argument value) (counted)
   (effects write) (obligation "Visits exactly the valid borrowed pointer slots; retains no frame/context."))
 (record "Frame" (function trace_frame) (context "TraceContext") (receiver write)
  (field left (borrow read nullable) (owner atoms) (visit atom))
  (field right (borrow read nullable) (owner atoms) (visit atom))
  (field items (borrow write nullable) (owner slots) (visit span) (count len) (when active))
  (field len value)
  (field episode (borrow read) (owner episode) (keep "Retained by the outer episode and excluded from atom evacuation."))
  (field active value)))
'''

HOST = r'''
#include "control.h"
#include "visitor.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>

static Atom *copy(TraceContext *ctx,Atom *source) {
    if(!source)return NULL;
    if(source==&ctx->destination[0] || source==&ctx->destination[1])return source;
    assert(source==&ctx->source[0] || source==&ctx->source[1]);
    size_t at=source==&ctx->source[1];
    if(ctx->copied[at])return &ctx->destination[at];
    ctx->copied[at]=true;
    ctx->destination[at].value=source->value;
    ctx->destination[at].next=copy(ctx,source->next);
    return &ctx->destination[at];
}
bool move_atom(TraceContext *ctx,Atom **slot) {
    if(++ctx->calls==ctx->fail_at)return false;
    *slot=copy(ctx,*slot);return true;
}
bool move_span(TraceContext *ctx,Atom **slots,size_t count) {
    for(size_t i=0;i<count;++i)if(!move_atom(ctx,&slots[i]))return false;
    return true;
}
#if MACRO_STYLE
static bool invoke(TraceContext *context,Frame *frame) {
    TRACE_FRAME(context,frame,goto failed);
    return true;
failed:return false;
}
#else
#define invoke trace_frame
#endif
int main(void) {
    Atom source[2]={{7,NULL},{11,NULL}},destination[2]={{0}};
    source[0].next=&source[1];source[1].next=&source[0];
    Episode episode={91};
    Atom *slots[3]={&source[0],&source[1],&source[0]};
    TraceContext context={source,destination,{false,false},0,0};
    Frame frame={&source[0],&source[0],slots,3,&episode,true};
    assert(invoke(&context,&frame));
    assert(context.calls==5 && frame.left==&destination[0] && frame.right==frame.left);
    assert(destination[0].next==&destination[1] && destination[1].next==&destination[0]);
    assert(slots[0]==frame.left && slots[1]==&destination[1] && slots[2]==frame.left);
    assert(frame.episode==&episode && episode.identity==91);
    context.calls=0;frame.left=frame.right=&source[0];frame.items=NULL;frame.len=1;
    assert(!invoke(&context,&frame));assert(!context.calls && frame.left==&source[0]);
    frame.items=slots;frame.len=SIZE_MAX;
    assert(!invoke(&context,&frame));assert(!context.calls && frame.right==&source[0]);
    frame.items=NULL;frame.len=0;
    assert(invoke(&context,&frame) && context.calls==2);
    frame.active=false;frame.items=NULL;frame.len=SIZE_MAX;context.calls=0;
    assert(invoke(&context,&frame) && context.calls==2);
    frame.active=true;frame.len=0;frame.left=frame.right=&source[0];
    context.calls=0;context.fail_at=2;
    assert(!invoke(&context,&frame));
    assert(frame.left==&destination[0] && frame.right==&source[0]);
    return 0;
}
'''


def run(command, *, cwd=ROOT, accepted=True):
    result = subprocess.run(list(map(str,command)),cwd=cwd,capture_output=True)
    assert (result.returncode == 0) == accepted,(command,result.returncode,result.stderr.decode(errors='replace'))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cesta',type=Path,default=ROOT/'build/cesta')
    parser.add_argument('--work',type=Path,default=ROOT/'build/record-visitors')
    args=parser.parse_args();binary=args.cesta.resolve();work=args.work.resolve()
    work.mkdir(parents=True,exist_ok=True)
    source=work/'control.h';source.write_text(HEADER)
    host=work/'host.c';host.write_text(HOST)
    outputs=[];refusals=routes=0
    for style in ('function','macro'):
        schema=SCHEMA if style=='function' else SCHEMA.replace('(function trace_frame)','(macro TRACE_FRAME)')
        description=work/'control.records';description.write_text(schema)
        generated=work/'visitor.h';facts=work/'visitor.facts'
        command=[binary,'record-visitor','--schema',description,'-o',generated,'--facts',facts,'--cc']
        projections=[]
        for compiler in ('gcc','clang'):
            run([*command,compiler,'-std=c11',source])
            projections.append(generated.read_bytes());outputs.append(facts.read_bytes())
            assert b'Frame' in facts.read_bytes() and b'Native policies' in generated.read_bytes()
            for opt in ('-O0','-O2','-O1'):
                sanitizer=opt=='-O1';program=work/(style+'-'+compiler+opt)
                flags=['-std=c11',opt,'-g0','-Wall','-Wextra','-Werror','-DMACRO_STYLE='+str(int(style=='macro'))]
                if sanitizer:flags+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer']
                run([compiler,*flags,host,'-o',program]);run([program]);routes+=1
            before=generated.read_bytes()
            run([*command,compiler,'-std=c11',source]);assert generated.read_bytes()==before
        assert projections[0]==projections[1]
    assert outputs[0]==outputs[1] and outputs[2]==outputs[3]

    negative={
        'missing_field':SCHEMA.replace('  (field right (borrow read nullable) (owner atoms) (visit atom))\n',''),
        'extra_field':SCHEMA.replace('(field right ','(field absent '),
        'duplicate_field':SCHEMA.replace('(field right ','(field left '),
        'wrong_owner':SCHEMA.replace('(field left (borrow read nullable) (owner atoms)','(field left (borrow read nullable) (owner episode)'),
        'unknown_owner':SCHEMA.replace('(owner atoms) (owner slots)','(owner nowhere) (owner slots)'),
        'wrong_type':SCHEMA.replace('(type "Atom *")','(type "Episode *")'),
        'read_receiver':SCHEMA.replace('(receiver write)','(receiver read)'),
        'owning_write':SCHEMA.replace('(field left (borrow read nullable)','(field left (owns heap)'),
        'readonly_slots':SCHEMA.replace('(field items (borrow write nullable)','(field items (borrow read nullable)'),
        'value_pointer':SCHEMA.replace('(field left (borrow read nullable) (owner atoms) (visit atom))','(field left value)'),
        'reference_scalar':SCHEMA.replace('(field len value)','(field len (borrow read) (owner atoms) (keep "wrong"))'),
        'missing_obligation':SCHEMA.replace('(obligation "Copies shared/cyclic atoms and updates its borrowed slot; retains no frame/context.")',''),
        'missing_count':SCHEMA.replace('(count len)',''),
        'missing_guard':SCHEMA.replace('(when active)','(when absent)'),
        'wrong_signature':SCHEMA.replace('(call move_atom)','(call move_span)'),
    }
    good=work/'control.records';good.write_text(SCHEMA)
    target=work/'visitor.h'
    run([binary,'record-visitor','--schema',good,'-o',target,'--cc','gcc','-std=c11',source])
    saved=target.read_bytes()
    for name,changed in negative.items():
        bad=work/(name+'.records');bad.write_text(changed)
        result=run([binary,'record-visitor','--schema',bad,'-o',target,'--cc','gcc','-std=c11',source],accepted=False)
        assert result.returncode==1 and str(bad).encode() in result.stderr,(name,result.stderr)
        assert target.read_bytes()==saved,name
        refusals+=1
    # A newly added pointer cannot silently vanish from the traversal contract.
    changed_header=work/'changed.h';changed_header.write_text(HEADER.replace('bool active;','bool active;Atom *forgotten;'))
    run([binary,'record-visitor','--schema',good,'-o',target,'--cc','gcc','-std=c11',changed_header],accepted=False)
    assert target.read_bytes()==saved;refusals+=1
    # The generated C independently rejects a same-size pointer-type change.
    drift=work/'drift';drift.mkdir(exist_ok=True)
    (drift/'control.h').write_text(HEADER.replace('Atom *left,*right;','Episode *left;Atom *right;'))
    (drift/'visitor.h').write_bytes(saved)
    (drift/'probe.c').write_text('#include "control.h"\n#include "visitor.h"\n')
    for compiler in ('gcc','clang'):
        result=run([compiler,'-std=c11','-fsyntax-only',drift/'probe.c'],accepted=False)
        assert b'record visitor field type drift' in result.stderr;refusals+=1
    for output in (source,good):
        original=output.read_bytes()
        run([binary,'record-visitor','--schema',good,'-o',output,'--cc','gcc','-std=c11',source],accepted=False)
        assert output.read_bytes()==original;refusals+=1

    # A read receiver can call a const-qualified native reader. Qualifier
    # removal and unsafe conversion through two pointer levels must refuse.
    read_header=work/'read.h'
    header_text=('typedef struct ReadContext {unsigned seen;} ReadContext;\n'
                 'typedef struct Holder {unsigned *item;} Holder;\n'
                 '_Bool observe(ReadContext *,const unsigned *);\n')
    read_header.write_text(header_text)
    read_schema=work/'read.records'
    read_text='''(cesta record-visitors/1 (owner values)
      (visitor observe (owner values) (type "unsigned *") (call observe)
        (argument value) (effects read) (obligation "Borrows the value during inspection."))
      (record "Holder" (function read_holder) (context "ReadContext") (receiver read)
        (field item (borrow read) (owner values) (visit observe))))'''
    read_schema.write_text(read_text)
    read_output=work/'read_visit.h'
    read_host=work/'read.c'
    read_host.write_text('#include "read.h"\n#include "read_visit.h"\n'
        '_Bool observe(ReadContext *c,const unsigned *p){c->seen=*p;return 1;}\n'
        'int main(void){unsigned n=7;Holder h={&n};ReadContext c={0};'
        'return !(read_holder(&c,&h)&&c.seen==7);}\n')
    for compiler in ('gcc','clang'):
        run([binary,'record-visitor','--schema',read_schema,'-o',read_output,'--cc',compiler,read_header])
        program=work/('read-'+compiler)
        run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',read_host,'-o',program])
        run([program]);routes+=1
    for label,changed_header,changed_schema in [
        ('const_loss',header_text.replace('unsigned *item','const unsigned *item').replace('const unsigned *);','unsigned *);'),
         read_text.replace('(type "unsigned *")','(type "const unsigned *")')),
        ('deep_const',header_text.replace('unsigned *item','unsigned **item').replace('const unsigned *);','const unsigned **);'),
         read_text.replace('(type "unsigned *")','(type "unsigned **")'))]:
        bad_header=work/(label+'.h');bad_header.write_text(changed_header)
        bad_schema=work/(label+'.records');bad_schema.write_text(changed_schema)
        before=read_output.read_bytes()
        result=run([binary,'record-visitor','--schema',bad_schema,'-o',read_output,'--cc','gcc',bad_header],accepted=False)
        assert b'field argument expects' in result.stderr and b'; supplied ' in result.stderr
        assert read_output.read_bytes()==before;refusals+=1
    array_header=('typedef struct ReadContext {unsigned seen;} ReadContext;\n'
                  'typedef const unsigned ReadArray[2];\n'
                  'typedef struct Holder {ReadArray *item;} Holder;\n'
                  '_Bool observe(ReadContext *,const void *);\n')
    read_header.write_text(array_header)
    read_schema.write_text(read_text.replace('(type "unsigned *")','(type "ReadArray *")'))
    read_host.write_text('#include "read.h"\n#include "read_visit.h"\n'
        '_Bool observe(ReadContext*c,const void*p){const unsigned*v=p;c->seen=v[0];return 1;}\n'
        'int main(void){ReadArray n={7,11};Holder h={&n};ReadContext c={0};'
        'return !(read_holder(&c,&h)&&c.seen==7);}\n')
    for compiler in ('gcc','clang'):
        run([binary,'record-visitor','--schema',read_schema,'-o',read_output,'--cc',compiler,read_header])
        program=work/('array-read-'+compiler)
        run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',read_host,'-o',program]);run([program]);routes+=1
        before=read_output.read_bytes()
        bad_header=work/'array-loss.h';bad_header.write_text(array_header.replace('const void *);','void *);'))
        result=run([binary,'record-visitor','--schema',read_schema,'-o',read_output,'--cc',compiler,bad_header],accepted=False)
        assert b'field argument expects' in result.stderr and read_output.read_bytes()==before;refusals+=1
    (work/'summary.json').write_text(json.dumps({'runtime_routes':routes,'refusals':refusals,
        'shared_references_cycles':True,'count_preflight_preserves_on_refusal':True,
        'partial_native_failure_explicit':True,'code_sha256':hashlib.sha256(saved).hexdigest()},indent=2)+'\n')
    print(f'record visitors: {routes} GCC/Clang routes; shared/cyclic relocation, counts/guards, '
          f'functions/macros and {refusals} contract/header/output refusals',flush=True)


if __name__=='__main__':main()
