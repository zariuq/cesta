"""Generated scalar guards against mathematical and Lean operational controls."""

import argparse
import random
import subprocess
import tempfile
from pathlib import Path

from checked_spans import BINARY, ROOT, run


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admit',action='store_true')
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    parser.add_argument('--lean-memory-mb',type=int)
    args=parser.parse_args()
    work=Path(tempfile.mkdtemp(prefix='scalar-operations-',dir=ROOT/'build'))
    types={'u8':(8,'uint8_t'),'u16':(16,'uint16_t'),'u32':(32,'uint32_t'),
           'u64':(64,'uint64_t'),'usize':(64,'size_t')}
    forms=['(cesta checked-c/1 (module operations) (failure operations_failure)']
    host=['#include "operations.h"','#include <setjmp.h>','#include <stdio.h>',
          'static jmp_buf failure;','void operations_failure(void){longjmp(failure,1);}',
          'int main(void){static unsigned checks=0;']
    proofs=['import Mettapedia.GSLT.LanguageDef.CheckedScalarOperations',
            'open Mettapedia.GSLT.LanguageDef.CheckedScalarOperations',
            'set_option maxRecDepth 200000','set_option maxHeartbeats 0']
    rng=random.Random(261109)
    total=0
    for ty,(bits,ctype) in types.items():
        limit=(1<<bits)-1
        values=sorted(set((0,1,2,limit//2,limit//2+1,limit-1,limit)))
        pairs=[(a,b) for a in values for b in values]
        pairs += [(rng.randrange(limit+1),rng.randrange(limit+1)) for _ in range(32)]
        for op in ('add','sub','mul','div','rem','shl','shr'):
            rhs='usize' if op in ('shl','shr') else ty
            forms.append(f'(fn {op}_{ty} (params (a {ty}) (b {rhs})) (returns {ty}) (effects) (body (return ({op} a b))))')
            cases=pairs if rhs==ty else [(a,b) for a in values for b in (0,1,bits-1,bits,bits+1)]
            lean=[]
            for a,b in cases:
                if op=='add': good=a+b<=limit;value=a+b;term=f'checkedAdd {limit} {a} {b}'
                elif op=='sub':good=a>=b;value=a-b;term=f'checkedSub {limit} {a} {b}'
                elif op=='mul':good=a*b<=limit;value=a*b;term=f'checkedMul {limit} {a} {b}'
                elif op=='div':good=b!=0;value=a//b if b else 0;term=f'checkedDivision false {a} {b}'
                elif op=='rem':good=b!=0;value=a%b if b else 0;term=f'checkedDivision true {a} {b}'
                else:
                    good=b<bits
                    value=((a<<b)&limit if op=='shl' else a>>b) if good else 0
                    term=f'checkedShift {bits} {"true" if op=="shl" else "false"} {a} {b}'
                call=f'operations_{op}_{ty}(({ctype})UINT64_C({a}),({"size_t" if rhs=="usize" else ctype})UINT64_C({b}))'
                if good:
                    host.append(f'if(setjmp(failure)){{return 1;}} if((uint64_t){call}!=UINT64_C({value})){{return 2;}} ++checks;')
                    lean.append(f'({term} == Outcome.value {value})')
                else:
                    host.append(f'if(!setjmp(failure)){{(void){call};return 3;}} ++checks;')
                    lean.append(f'({term} == Outcome.failed Failure.arithmetic)')
                total+=1
            proofs.append(f'example : [{", ".join(lean)}].all id = true := by decide')
    forms += ['(fn read (params (s (span u32 read)) (i usize)) (returns u32) (effects) (body (return (at s i))))',
              '(fn write (params (s (span u32 write)) (i usize) (v u32)) (returns unit) (effects) (body (put s i v) (return)))',')']
    for index in range(4):
        host.append('{uint32_t data[4]={4,8,15,16};')
        if index<2:
            expected=(8,15)[index]
            host.append(f'if(setjmp(failure)){{return 4;}}if(operations_read((cesta_read_u32_span){{data+1,2}},{index})!={expected}){{return 5;}}')
            host.append(f'operations_write((cesta_write_u32_span){{data+1,2}},{index},23);')
            after=[4,8,15,16];after[index+1]=23
            proofs.append(f'example : checkedRead [4,8,15,16] 1 2 {index} = .value {expected} := by decide')
            proofs.append(f'example : checkedWrite [4,8,15,16] 1 2 {index} 23 = .value {after} := by decide')
        else:
            host.append(f'if(!setjmp(failure)){{(void)operations_read((cesta_read_u32_span){{data+1,2}},{index});return 6;}}')
            host.append(f'if(!setjmp(failure)){{operations_write((cesta_write_u32_span){{data+1,2}},{index},23);return 7;}}')
            after=[4,8,15,16]
            proofs.append(f'example : checkedRead [4,8,15,16] 1 2 {index} = .failed .bounds := by decide')
            proofs.append(f'example : checkedWrite [4,8,15,16] 1 2 {index} 23 = .failed .bounds := by decide')
        host.append('if('+ '||'.join(f'data[{i}]!={n}' for i,n in enumerate(after))+'){return 8;}checks+=2;}')
        total+=2
    host.append(f'printf("%u primitive executions\\n",checks);return checks=={total}?0:9;}}')
    source=work/'operations.cesta';source.write_text('\n'.join(forms)+'\n')
    header=work/'operations.h';exported=work/'operations.c';driver=work/'host.c'
    driver.write_text('\n'.join(host)+'\n')
    run([BINARY,'checked','header',source,'-o',header])
    run([BINARY,'export-c',source,'-o',exported])
    for cc in ('gcc','clang'):
        for sanitized in (False,True):
            binary=work/f'{cc}-{sanitized}'
            flags=['-std=c11','-O1','-Wall','-Wextra','-Werror']
            if sanitized:flags+=['-fsanitize=address,undefined','-fno-pie','-no-pie','-fno-sanitize-recover=all']
            run([cc,*flags,exported,driver,'-o',binary]);run([binary])
    if args.admit:
        proof=work/'Operations.lean';proof.write_text('\n'.join(proofs)+'\n')
        command=['lake','env','lean','-DwarningAsError=true']
        if args.lean_memory_mb is not None:command+=['-M',str(args.lean_memory_mb)]
        result=subprocess.run([*command,str(proof)],cwd=args.lean_root,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
    print(f'checked operations: {total} scalar and memory cases; GCC/Clang and sanitizers; '
          + ('independent Lean operational controls checked' if args.admit else 'mathematical controls passed'))


if __name__=='__main__':main()
