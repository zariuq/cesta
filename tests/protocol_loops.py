"""Bounded protocol iteration against independent execution and C compilers."""
import copy
from pathlib import Path
import tempfile

from protocols import CESTA, ROOT, OUTCOMES, declaration, module, run
from oracles.pro_compact_cesta import Sym

def semantics(data, bits, budget):
    hooks = {str(h[1]): h for h in data[3][1:]}
    resources = {str(r[1]): r for r in data[4][1:]}
    used, world, trace = 0, 17, []
    def event(name, boolean):
        nonlocal used, world
        reply = (bits >> used) & 1 if boolean else 2
        used += int(boolean)
        for byte in str(name).encode(): world = (world * 33 + byte) & ((1 << 64)-1)
        world = (world * 33 + reply) & ((1 << 64)-1)
        trace.append(f'{name}:{reply}')
        return reply
    def execute(body):
        kind = str(body[0])
        if kind == 'Return': return str(body[1])
        if kind == 'Continue': return None
        if kind == 'Loop':
            # A later hook may change the field; the bound is a snapshot.
            count = budget if isinstance(body[1],list) else int(body[1])
            for _ in range(count):
                stopped = execute(body[2])
                if stopped is not None: return stopped
            return execute(body[3])
        if kind == 'Call':
            reply = event(hooks[str(body[1])][2],True)
            return execute(body[2 if reply else 3])
        resource = resources[str(body[1])]
        if kind == 'Acquire':
            reply = event(resource[2],True)
            return execute(body[2 if reply else 3])
        event(resource[3 if kind == 'Release' else 4],False)
        return execute(body[2])
    outcome = execute(data[6][1])
    return f'{OUTCOMES.index(outcome)} {used} {world} '+ ' '.join(trace)

def harness(header, checked, failure_override=False):
    return r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct WaitContext {
    uint64_t budget;
    unsigned bits, used;
    uint64_t world;
    char trace[4096];
    size_t len;
    bool owned, complete;
} WaitContext;
static bool event(WaitContext *s, const char *name, bool boolean) {
    unsigned reply = boolean ? (s->bits >> s->used++) & 1u : 2;
    for (const unsigned char *p=(const unsigned char *)name; *p; ++p) s->world=s->world*33+*p;
    s->world=s->world*33+reply;
    int n=snprintf(s->trace+s->len,sizeof(s->trace)-s->len,"%s%s:%u",s->len?" ":"",name,reply);
    if (n<0 || (size_t)n>=sizeof(s->trace)-s->len) abort();
    s->len+=(size_t)n;
    return reply!=0;
}
static bool wait_acquire(WaitContext *s) { s->owned=event(s,"wait_acquire",true); return s->owned; }
static bool wait_ready(WaitContext *s) { s->complete=event(s,"wait_ready",true); return s->complete; }
static bool wait_failed(WaitContext *s) { return event(s,"wait_failed",true); }
static bool wait_yield(WaitContext *s) { s->budget=0; return event(s,"wait_yield",true); }
static void wait_release(WaitContext *s) { s->owned=false; (void)event(s,"wait_release",false); }
static void wait_transfer(WaitContext *s) { s->owned=false; (void)event(s,"wait_transfer",false); }
bool wait_owned(WaitContext *s) { return s->owned; }
bool wait_complete(WaitContext *s) { return s->complete; }
''' + ('#define wait_run_CHECKED 1\n' if checked else '') + (r'''
static _Noreturn void checked_failure(void) { exit(77); }
#define wait_run_CHECK_FAILURE(ctx, kind, id, expected) checked_failure()
''' if failure_override else '') + f'#include "{header.name}"\n' + r'''
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    WaitContext s={.bits=(unsigned)strtoul(argv[1],NULL,10),.budget=strtoull(argv[2],NULL,10),.world=17};
    unsigned result=wait_run(&s);
    printf("%u %u %llu %s\n",result,s.used,(unsigned long long)s.world,s.trace);
    return 0;
}
'''

def main():
    work = Path(tempfile.mkdtemp(prefix='protocol-loops-',dir=ROOT/'build'))
    name,data = declaration(ROOT/'protocols/Wait.module')
    header = work/'wait.h'
    run([CESTA,'protocol','export',ROOT/'protocols/Wait.module','Protocols','-o',header,'--plan',work/'wait.plan'])
    for compiler in ('gcc','clang'):
        for checked,override in [(False,False),(True,False),(True,True)]:
            source = work/f'{compiler}-{checked}-{override}.c'
            source.write_text(harness(header,checked,override))
            binary = source.with_suffix('')
            run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Wpedantic','-Werror',source,'-o',binary])
            for budget in (0,1,3):
                for bits in range(128):
                    actual = run([binary,bits,budget]).stdout.rstrip('\n')
                    assert actual == semantics(data,bits,budget),(compiler,checked,budget,bits,actual)
            print(compiler,'checked='+str(checked),'custom_failure='+str(override),'bounded traces passed')
    # Inner exhaustion continues the outer loop; its C continue must target
    # the outer countdown after the inner countdown's scope has ended.
    nested=copy.deepcopy(data)
    original=copy.deepcopy(nested[6][1][2])
    nested[6][1][2]=[Sym('Loop'),Sym('2'),
        [Sym('Loop'),Sym('3'),original[2],[Sym('Continue')]],original[3]]
    path=work/'nested.module';path.write_text(module(name,nested))
    nested_header=work/'nested.h'
    run([CESTA,'protocol','export',path,'Protocols','-o',nested_header])
    for compiler in ('gcc','clang'):
        for checked in (False,True):
            source=work/(compiler+'-nested-'+str(checked)+'.c')
            source.write_text(harness(nested_header,checked))
            binary=source.with_suffix('')
            run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Wpedantic','-Werror',source,'-o',binary])
            for bits in (1,3,5,9,17,65,4097,65537):
                actual=run([binary,bits,4]).stdout.rstrip('\n')
                assert actual==semantics(nested,bits,4),(compiler,checked,bits,actual)
    print('nested loops: inner exhaustion, outer continuation and all early outcomes passed')
    negatives = []
    def reject(label,body):
        mutant = copy.deepcopy(data); mutant[6][1] = body
        path=work/(label+'.module'); path.write_text(module(name,mutant))
        run([CESTA,'protocol','check',path,'Protocols'],ok=False)
        negatives.append(label)
    reject('outside',[Sym('Continue')])
    reject('overflow',[Sym('Loop'),Sym(str(1<<64)),[Sym('Continue')],[Sym('Return'),Sym('done')]])
    reject('leading_zero',[Sym('Loop'),Sym('01'),[Sym('Continue')],[Sym('Return'),Sym('done')]])
    reject('negative',[Sym('Loop'),Sym('-1'),[Sym('Continue')],[Sym('Return'),Sym('done')]])
    reject('leaked_owner',[Sym('Acquire'),Sym('request'),
        [Sym('Loop'),Sym('1'),[Sym('Release'),Sym('request'),[Sym('Continue')]],
         [Sym('Release'),Sym('request'),[Sym('Return'),Sym('done')]]],
        [Sym('Return'),Sym('resource_failure')]])
    reject('decline_after_wait',[Sym('Loop'),Sym('0'),[Sym('Continue')],[Sym('Return'),Sym('declined')]])
    # The compiler checks the actual C member type, not a width guessed from a name.
    for kind in ('unsigned','double','volatile uint64_t'):
        source=work/('wrong-'+kind.replace(' ','-')+'.c')
        source.write_text(harness(header,False).replace('uint64_t budget;',kind+' budget;'))
        run(['gcc','-std=c11','-c',source,'-o',source.with_suffix('.o')],ok=False)
    large=copy.deepcopy(data)
    large[6][1][2][1] = Sym(str((1<<64)-1))
    path=work/'large.module'; path.write_text(module(name,large))
    run([CESTA,'protocol','export',path,'Protocols','-o',work/'large.h'])
    assert (work/'large.h').stat().st_size < 20000,'literal budget was unrolled'
    print('protocol loops:',len(negatives),'invalid declarations and three invalid C member types refused')

if __name__ == '__main__': main()
