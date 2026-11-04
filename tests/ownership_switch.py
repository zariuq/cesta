"""Grouped labels preserve native execution and emit admitted ownership arms."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import ownership_core

ROOT=Path(__file__).resolve().parent.parent
CASES={
    'switch_grouped_returns':[9,7,7,9,9],
    'switch_grouped_breaks':[9,7,7,9,9],
    'switch_default_group':[7,7,7,7,7],
    'switch_grouped_loop':[9,2,2,9,9],
    'switch_disjoint_loan':[10,4,4,10,10],
}


def run(command,cwd=ROOT):
    result=subprocess.run(list(map(str,command)),cwd=cwd,capture_output=True)
    assert result.returncode==0,(command,result.stderr[-5000:])
    return result.stdout


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cesta',type=Path,default=ROOT/'build/cesta')
    parser.add_argument('--output',type=Path,default=ROOT/'build/ownership-switch')
    parser.add_argument('--lean-root',type=Path);args=parser.parse_args()
    work=args.output.resolve();work.mkdir(parents=True,exist_ok=True)
    harness=work/'main.c';harness.write_text('#include <stdio.h>\nint f(int);int main(void){int input[]={-1,0,1,2,99};for(int i=0;i<5;++i)printf("%d\\n",f(input[i]));return 0;}\n')
    records=[];admitted=[]
    if args.lean_root:import ownership_admission
    for name,expected in CASES.items():
        directory=work/name;directory.mkdir(exist_ok=True)
        source,contract=ownership_core.POSITIVE[name]
        raw=directory/'plain.c';raw.write_text(source)
        authored=directory/'source.cesta';authored.write_text(ownership_core.document(source,contract))
        plans={}
        for compiler in ('gcc','clang'):
            exported=directory/(compiler+'.c');plan=directory/(compiler+'.plan')
            run([args.cesta,'export-c',authored,'--std=c11','--cc',compiler,'-o',exported,'--plan',plan])
            plans[hashlib.sha256(plan.read_bytes()).hexdigest()]=plan
            for mode,flags in [('O0',['-O0']),('O2',['-O2']),('SAN',['-O1','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
                for route,path in [('native',authored),('exported',exported),('plain',raw)]:
                    binary=directory/(compiler+'-'+mode+'-'+route)
                    driver=[args.cesta,'cc','--cc',compiler]if route=='native'else[compiler]
                    run([*driver,'-std=c11','-Wall','-Wextra','-Wpedantic','-Werror',*flags,path,harness,'-o',binary])
                    observed=list(map(int,run([binary]).split()))
                    assert observed==expected,(name,compiler,mode,route,observed)
                    records.append(dict(case=name,compiler=compiler,mode=mode,route=route))
        if args.lean_root:
            for index,plan in enumerate(plans.values()):
                record,=ownership_admission.parse(plan.read_bytes())
                admitted.append(ownership_admission.admit_record(record,directory/f'Admission{index}.lean',args.lean_root.resolve()))
    (work/'summary.json').write_text(json.dumps(dict(executions=records,admission=admitted),indent=2)+'\n')
    print('grouped switches:',len(records),'native/export/plain executions;',sum(x['functions']for x in admitted),'actual plans admitted')


if __name__=='__main__':main()
