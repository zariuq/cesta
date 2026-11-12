"""Ordinary C post-test loops and forward cleanup jumps, with live resources."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from ownership_core import ROOT, BINARY, document
from oracles.pro_compact_cesta import parse

HEAP = 'extern void *reserve(unsigned long); extern void dispose(void *);\n'
ALLOCATOR = '(allocator heap reserve dispose (obligation "Fresh storage; release accepts null and consumes each allocation once."))'

POSITIVE = {
    'do_initializes': ('int f(int n){int x;do{x=n+7;}while(0);return x;}', '', 'assert(f(n)==n+7);'),
    'do_borrow_initializes': ('int f(int n){const int*p;do{p=&n;}while(0);return *p;}', '', 'assert(f(n)==n);'),
    'do_borrowed_result': ('const int*pick(const int*p){const int*q;do{q=p;}while(0);return q;}int f(int n){return *pick(&n);}', '(function pick (returns-borrow 0))', 'assert(f(n)==n);'),
    'do_runs_once': ('int f(int n){int x=0;do{++x;}while(x<n);return x;}', '', 'assert(f(n)==(n>1?n:1));'),
    'do_condition_effect': ('int test(int*p){++*p;return *p<3;} int f(int n){int tests=0,body=n;do{++body;}while(test(&tests));return body*10+tests;}', '', 'assert(f(n)==(n+3)*10+3);'),
    'do_continue_test': ('int test(int*p){++*p;return *p<3;} int f(int n){int tests=0,body=n;do{++body;continue;}while(test(&tests));return body*10+tests;}', '', 'assert(f(n)==(n+3)*10+3);'),
    'do_nested_continue': ('int test(int*p){++*p;return *p<3;} int f(int n){int tests=0,body=n;do{++body;if(body>0){if(tests<2)continue;}body+=10;}while(test(&tests));return body*10+tests;}', '', 'assert(f(n)==(n+13)*10+3);'),
    'do_break_skips_test': ('int f(int n){int*p=0;do{break;}while(*p);return n;}', '', 'assert(f(n)==n);'),
    'do_break_initializes': ('int f(int n){int x;int*p=0;do{x=n+2;break;}while(*p);return x;}', '', 'assert(f(n)==n+2);'),
    'do_false_continue': ('int f(int n){do{++n;continue;}while(0);return n;}', '', 'assert(f(n)==n+1);'),
    'do_scoped_borrow': ('int f(int n){do{const int*p=&n;(void)*p;}while(n-- > 2);return n;}', '', 'assert(f(n)==(n>2?1:n-1));'),
    'do_nested_loop_break': ('int f(int n){int x=0;do{int i=0;while(i++<3){if(i==2)break;++x;}++x;}while(x<n);return x;}', '', 'assert(f(n)==(n>0?2*((n+1)/2):2));'),
    'do_macro_in_while': ('#define ADD_ONCE(v) do { ++(v); } while (0)\nint f(int n){int x=0;while(x<n){ADD_ONCE(x);if(x%2)continue;}return x;}', '', 'assert(f(n)==n);'),
    'do_nested_do': ('int f(int n){int i=0,x=n;do{do{++x;break;}while(0);if(++i<3)continue;}while(i<3);return x;}', '', 'assert(f(n)==n+3);'),
    'do_switch_continue': ('int f(int n){int x=0,i=0;do{++i;switch(i){case 1:continue;default:++x;break;}++x;}while(i<3);return n+x;}', '', 'assert(f(n)==n+4);'),
    'do_single_release': (HEAP+'void consume(int*p){do{dispose(p);}while(0);} int f(int n){int*p=(int*)reserve(4);if(!p){dispose(p);return 0;}*p=n;consume(p);return n;}', ALLOCATOR+' (function consume (owns 0 heap))', 'assert(f(n)==n);'),
    'do_break_release': (HEAP+'void consume(int*p){do{dispose(p);break;}while(*p);} int f(int n){int*p=(int*)reserve(4);if(!p){dispose(p);return 0;}*p=n;consume(p);return n;}', ALLOCATOR+' (function consume (owns 0 heap))', 'assert(f(n)==n);'),
    'do_return': ('int f(int n){do{return n+1;}while(1);}', '', 'assert(f(n)==n+1);'),
    'goto_cleanup': ('int f(int n){int x=1;if(n%2)goto cleanup;x+=3;cleanup:return x;}', '', 'assert(f(n)==(n%2?1:4));'),
    'goto_chain': ('int f(int n){int x=n;if(n%2)goto first;x+=10;goto last;first:x+=3;goto last;last:return x;}', '', 'assert(f(n)==n+(n%2?3:10));'),
    'goto_two_labels': ('int f(int n){if(n%2)goto first;goto second;first:second:return n;}', '', 'assert(f(n)==n);'),
    'goto_skips_initializer': ('int f(int n){goto cleanup;int x=n+100;cleanup:x=n+7;return x;}', '', 'assert(f(n)==n+7);'),
    'goto_from_loop': ('int f(int n){int x=0;while(n--){if(n==2)goto cleanup;++x;}cleanup:return x;}', '', 'assert(f(n)==(n>=3?n-3:n));'),
    'goto_from_do': ('int f(int n){int x=0;do{if(n%2)goto cleanup;++x;}while(0);cleanup:return x;}', '', 'assert(f(n)==(n%2?0:1));'),
    'goto_from_switch': ('int f(int n){int x=0;switch(n%2){case 0:goto cleanup;default:x=3;break;}cleanup:return x;}', '', 'assert(f(n)==(n%2?3:0));'),
    'goto_ends_child_borrow': (HEAP+'int f(int n){int*p=(int*)reserve(4);if(!p){dispose(p);return 0;}*p=n;{const int*q=p;if(*q==n)goto cleanup;}cleanup:dispose(p);return n;}', ALLOCATOR, 'assert(f(n)==n);'),
    'goto_partial_acquisition': (HEAP+'int f(int n){int*p=(int*)reserve(4);if(!p){dispose(p);return 0;}int*q=(int*)reserve(4);if(!q){dispose(q);goto out_p;}*p=n;*q=n+1;if(n%2)goto out_q;*q+=1;out_q:dispose(q);out_p:dispose(p);return n;}', ALLOCATOR, 'assert(f(n)==n);'),
    'goto_consumed_input': (HEAP+'void consume(int*p,int n){if(n%2)goto out;*p=n;out:dispose(p);}int f(int n){int*p=(int*)reserve(4);if(!p){dispose(p);return 0;}consume(p,n);return n;}', ALLOCATOR+' (function consume (owns 0 heap))', 'assert(f(n)==n);'),
    'goto_borrowed_result': ('const int*pick(const int*p,int n){if(n)goto out;const int*q=p;return q;out:return p;}int f(int n){return *pick(&n,n%2);}', '(function pick (returns-borrow 0))', 'assert(f(n)==n);'),
    'goto_void_tail': ('void set(int*p,int n){if(n%2)goto out;*p+=2;out:*p+=1;}int f(int n){int x=0;set(&x,n);return x;}', '', 'assert(f(n)==(n%2?1:3));'),
}

NEGATIVE = {
    'continue_reads_released': (HEAP+'void f(int*p){do{dispose(p);continue;}while(*p);}', ALLOCATOR+' (function f (owns 0 heap))', 'moved'),
    'repeat_double_release': (HEAP+'void f(int*p,int n){do{dispose(p);}while(n);}', ALLOCATOR+' (function f (owns 0 heap))', 'moved'),
    'continue_uninitialized_test': ('int f(int n){int x;do{if(n)continue;x=1;}while(x);return 0;}', '', 'disagree'),
    'condition_null': ('int f(void){int*p=0;do{}while(*p);return 0;}', '', 'null'),
    'do_dangling_field': ('const int*f(const int*p){const int*q=p;do{int n=7;q=&n;}while(0);return q;}', '(function f (returns-borrow 0))', 'outlives'),
    'goto_leak': (HEAP+'void f(int*p){goto out;dispose(p);out:return;}', ALLOCATOR+' (function f (owns 0 heap))', 'without release'),
    'goto_double_release': (HEAP+'void f(int*p,int n){if(n){dispose(p);goto out;}out:dispose(p);}', ALLOCATOR+' (function f (owns 0 heap))', 'moved'),
    'goto_skips_pointer_init': ('int f(const int*p){goto out;const int*q=p;out:return *q;}', '', 'uninitialized'),
    'goto_skips_scalar_init': ('int f(void){goto out;int n=3;out:return n;}', '', 'uninitialized'),
    'goto_skips_output': ('void f(const int*p,const int**out){goto done;*out=p;done:return;}', '(function f (stores 1 0))', 'every return'),
    'goto_child_escape': ('const int*f(const int*p){const int*q=p;{int local=7;q=&local;goto out;}out:return q;}', '(function f (returns-borrow 0))', 'outlives'),
    'goto_child_owner_leak': (HEAP+'void f(void){{int*p=(int*)reserve(4);if(!p){dispose(p);return;}goto out;}out:return;}', ALLOCATOR, 'without release'),
    'backward_jump': ('int f(int n){again:if(n--)goto again;return 0;}', '', 'backward goto'),
    'enter_nested_scope': ('int f(int n){goto inner;{int x=n;inner:return x;}}', '', 'outer block'),
    'computed_jump': ('int f(int n){void*p=&&out;goto *p;out:return n;}', '', 'unsupported'),
}


def run(command, accepted=True, cwd=ROOT):
    result=subprocess.run(list(map(str,command)),cwd=cwd,capture_output=True,text=True)
    assert (result.returncode==0)==accepted,(command,result.returncode,result.stdout[-1000:],result.stderr[-4000:])
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admit',action='store_true')
    parser.add_argument('--case',action='append',default=[])
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    args=parser.parse_args()
    if set(args.case)-(POSITIVE.keys() | NEGATIVE.keys()):parser.error('unknown control-flow case')
    positive={k:v for k,v in POSITIVE.items() if not args.case or k in args.case}
    negative={k:v for k,v in NEGATIVE.items() if not args.case or k in args.case}
    work=Path(tempfile.mkdtemp(prefix='ownership-control-',dir=ROOT/'build'))
    # A deterministic failure-injection allocator, kept outside the checked bodies.
    provider=work/'provider.c'
    provider.write_text('''#include <stdlib.h>
#include <assert.h>
static unsigned live, calls, fail_at;
void *reserve(unsigned long n){++calls;if(calls==fail_at)return 0;void*p=malloc(n);if(p)++live;return p;}
void dispose(void*p){if(p){assert(live);--live;}free(p);}
void reset(unsigned failure){assert(!live);calls=0;fail_at=failure;}
void released(void){assert(!live);}
''')
    plans=[]; matrix=[]
    for name,(body,contract,expected) in positive.items():
        source=work/(name+'.cesta');source.write_text(document(body,contract))
        plan=work/(name+'.plan');exported=work/(name+'.c')
        run([BINARY,'export-c',source,'-o',exported,'--plan',plan])
        record,=parse(plan.read_bytes());plans.append((name,record))
        if name in ('do_borrowed_result','goto_partial_acquisition'):
            arm_c=work/(name+'-aapcs64.c');arm_plan=arm_c.with_suffix('.plan')
            run([BINARY,'export-c','--cc','clang',source,'-o',arm_c,'--plan',arm_plan,
                 '--','--target=aarch64-none-elf','-ffreestanding'])
            run(['clang','--target=aarch64-none-elf','-ffreestanding','-std=c11','-Werror','-fsyntax-only',arm_c])
            assert b'aapcs64' in arm_plan.read_bytes()
            arm_record,=parse(arm_plan.read_bytes());plans.append((name+'_aapcs64',arm_record))
        harness=work/(name+'-host.c')
        failure='assert(f(7)==(fail==1?0:7));' if body.startswith(HEAP) else '(void)f(7);'
        harness.write_text('#include <assert.h>\nint f(int); void reset(unsigned); void released(void);\n'
            'int main(void){for(int n=0;n<8;++n){reset(0);'+expected+'released();}\n'
            'for(unsigned fail=1;fail<=2;++fail){reset(fail);'+failure+'released();}return 0;}\n')
        for cc in ('gcc','clang'):
            for mode in ('O0','O2','sanitize'):
                flags=['-std=gnu11','-g0','-Wall','-Wextra','-Werror','-Wno-unused-label']
                flags+=['-O1','-fsanitize=address,undefined','-fno-pie','-no-pie'] if mode=='sanitize' else ['-'+mode]
                binary=work/f'{name}-{cc}-{mode}'
                run([cc,*flags,exported,harness,provider,'-o',binary]);run([binary])
                if mode!='sanitize':
                    authored=work/f'{name}-{cc}-{mode}-authored.o';plain=work/f'{name}-{cc}-{mode}-plain.o'
                    run([BINARY,'cc','--cc',cc,*flags,'-c',source,'-o',authored])
                    run([cc,*flags,'-c',exported,'-o',plain])
                    for obj in (authored,plain):run(['objcopy','-O','binary','--only-section=.text',obj,obj.with_suffix('.text')])
                    assert authored.with_suffix('.text').read_bytes()==plain.with_suffix('.text').read_bytes(),(name,cc,mode)
            matrix.append((name,cc))
    for name,(body,contract,message) in negative.items():
        path=work/(name+'.cesta');path.write_text(document(body,contract))
        plain=work/(name+'-plain.c');plain.write_text(body)
        for cc in ('gcc','clang'):
            run([BINARY,'cc','--cc',cc,'-std=gnu11','-fsyntax-only',plain])
            result=run([BINARY,'cc','--cc',cc,'-std=gnu11','-fsyntax-only',path],accepted=False)
            assert 'ownership' in result.stderr and message in result.stderr,(name,result.stderr)
    admission=[]
    if args.admit:
        from ownership_admission import admit_record, module_parts, Text
        for index,(name,record) in enumerate(plans):
            if record[1].data == b'cesta-ownership/6':
                old=copy.deepcopy(record);old[1]=Text(b'cesta-ownership/3')
                try:module_parts(old)
                except AssertionError as error:
                    assert 'post-test control requires version six' in str(error),error
                else:raise AssertionError('post-test plan accepted under an older schema')
            # The shared helper launches Lean from another project directory.
            # A relative output path must resolve at this call site.
            path=work/(name+'.lean')
            if index==0:path=Path(os.path.relpath(path))
            admission.append(admit_record(record,path,args.lean_root))
    (work/'summary.json').write_text(json.dumps(dict(positive=len(positive),negative=len(negative),
        cases=list(positive)+list(negative),
        compiler_routes=len(matrix)*3,admission=admission,
        compiler_sha256=hashlib.sha256(BINARY.read_bytes()).hexdigest()),indent=2)+'\n')
    print(f'ownership control: {len(positive)} executable cases, {len(negative)} source refusals; GCC/Clang O0/O2, sanitizers and exported instruction identity'+('; emitted plans admitted' if args.admit else ''),flush=True)
    print('Evidence:',work,flush=True)


if __name__=='__main__':main()
