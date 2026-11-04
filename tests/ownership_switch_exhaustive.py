"""Admit exhaustive switch plans, including releases on every return path."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import ownership_admission as native
from ownership_core import document,HEAP_C,HEAP_CONTRACT

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cesta',type=Path,default=ROOT/'build/cesta');parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia');parser.add_argument('--output',type=Path,default=ROOT/'build/ownership-switch-exhaustive');args=parser.parse_args()
    work=args.output.resolve();work.mkdir(parents=True,exist_ok=True)
    code=HEAP_C+'''
int grouped(int n){switch(n){case 0:case 1:return 7;default:return 9;}}
int nested(int n,int m){switch(n){case 0:switch(m){case 1:return 3;default:return 4;}default:return 9;}}
const char *label(int n){switch(n){case 0:return "zero";default:return "other";}}
void release(int n){int *p=(int *)reserve(4);if(!p){dispose(p);return;}switch(n){case 0:dispose(p);return;default:dispose(p);return;}}
int with_break(int n){switch(n){case 0:break;default:return 9;}return 7;}
'''
    source=work/'switch.cesta';source.write_text(document(code,HEAP_CONTRACT+' (function label (returns-static))'))
    plan=work/'switch.plan'
    subprocess.run([str(args.cesta.resolve()),'export-c',str(source),'--std=c11','-o',str(work/'switch.c'),'--plan',str(plan)],cwd=ROOT,check=True)
    record,=native.parse(plan.read_bytes());assert b'(switch-total'in plan.read_bytes()
    admitted=native.admit_record(record,work/'Switch.lean',args.lean_root.resolve())
    assert admitted['functions']==5 and admitted['module_admitted']is True
    foreign,functions=native.module_parts(record)
    release=next(item for item in functions if str(item[1])=='release')
    changed=copy.deepcopy(release)
    for event in native.walk(changed[-1][1:]):
        if event[0]=='switch-total':event[0]=native.Sym('switch')
    # Keep the actual callable table and the altered generated body visible to Lean.
    audit=work/'NoMatch.lean'
    audit.write_text('import SwitchParts.Names\nopen Mettapedia.GSLT.LanguageDef.Ownership\nopen SwitchParts\nset_option maxRecDepth 200000\nset_option maxHeartbeats 0\ndef altered : Function := '+native.function(changed)+'\nexample : admitFunction callables altered = false := by decide +kernel\n')
    subprocess.run(['lake','env','lean','--root='+str(work),audit],cwd=args.lean_root.resolve(),check=True,env={**os.environ,'LEAN_PATH':str(work)+os.pathsep+os.environ.get('LEAN_PATH','')})
    (work/'summary.json').write_text(json.dumps(dict(admitted=admitted,spurious_no_match_refused=True),indent=2)+'\n')
    print('ownership switches: five actual plans admitted; spurious no-match path refused')


if __name__=='__main__':main()
