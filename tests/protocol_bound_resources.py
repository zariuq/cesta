"""Resource-indexed protocol runtime, source controls and kernel admission."""
import argparse,copy,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path

from oracles.pro_compact_cesta import Sym,Text,parse,dumps
from protocol_admission import string,strings,source as core_source,body as core_body,checks,instrumented
from protocols import module,run

ROOT=Path(__file__).resolve().parent.parent
MODEL='Mettapedia.GSLT.LanguageDef.ExecutableProtocol'
RESOURCE_MODEL=MODEL+'Resources'
RESOURCE_NS=MODEL+'.Resources'

def normalize_body(value):
 result=copy.deepcopy(value);kind=str(result[0])
 aliases={'Claim':'Acquire','Resume':'Acquire','Discharge':'Release','TransferTo':'Transfer'}
 if kind in aliases:result[0]=Sym(aliases[kind])
 for i in range(2,len(result)):result[i]=normalize_body(result[i])
 return result

def base_data(record):
 hooks=[Sym('Hooks')]
 accesses=[]
 for h in record[3][1:]:
  optional={str(c[0]):c for c in h[7:]}
  plain=[*h[:7]]
  if 'Invalidates'in optional:plain.append(optional['Invalidates'])
  hooks.append(plain)
  accesses.append((str(h[1]),optional.get('Reads',[Sym('Reads')])[1:],
                   optional.get('Writes',[Sym('Writes')])[1:]))
 resources=[Sym('Resources')];owners=[]
 for r in record[4][1:]:
  clauses={str(c[0]):c for c in r[5:]}
  resources.append([*r[:5],clauses.get('AcquireRequires',[Sym('AcquireRequires')]),
                    clauses.get('TransferRequires',[Sym('TransferRequires')])])
  if 'BoundOwner'in clauses:owners.append((str(r[1]),str(clauses['BoundOwner'][1])))
 extras=[x for x in record[9:]if str(x[0])in('Instances','Dependencies','ExitRequires')]
 instances=next((x[1:]for x in extras if str(x[0])=='Instances'),[])
 dependencies=next((x[1:]for x in extras if str(x[0])=='Dependencies'),[])
 exits=next((x for x in extras if str(x[0])=='ExitRequires'),None)
 instrumentation=record[8];assert instrumentation[0]=='Instrumentation'
 data=[Sym('Protocol'),'cesta-protocol/1',record[2],hooks,resources,record[5],
       [Sym('Body'),normalize_body(record[6][1])]]
 if exits:data.append(exits)
 data.append(instrumentation[1])
 return data,accesses,instances,dependencies,owners

def bound_literal(name,data,accesses,instances,dependencies,owners):
 base=core_source(name,data)
 access='['+', '.join('⟨'+string(hook)+', '+strings(reads)+', '+strings(writes)+'⟩'
                      for hook,reads,writes in accesses)+']'
 deps='['+', '.join('⟨'+string(row[1])+', '+strings(row[2:])+'⟩'for row in dependencies)+']'
 held='['+', '.join('⟨'+string(resource)+', '+string(predicate)+'⟩'for resource,predicate in owners)+']'
 return '⟨'+base+', '+strings(instances)+', '+access+', '+deps+', '+held+'⟩'

def source_from_record(record):
 data=[Sym('Protocol'),Text(b'cesta-protocol/2'),*copy.deepcopy(record[2:7])]
 for item in record[9:]:
  if str(item[0])in('Instances','Dependencies','ExitRequires'):data.append(copy.deepcopy(item))
 data.append(copy.deepcopy(record[8][1]))
 return data

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cesta',type=Path,default=ROOT/'build/cesta')
 ap.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
 ap.add_argument('--output',type=Path,default=ROOT/'build/protocol-bound-resources');args=ap.parse_args()
 w=args.output.resolve();w.mkdir(parents=True,exist_ok=True);header=w/'bound.h';plan=w/'bound.plan'
 run([args.cesta,'protocol','export',ROOT/'protocols/BoundCurrent.module','Protocols','-o',header,'--plan',plan])
 runtime=[]
 for cc in('gcc','clang'):
  for checked in(0,1):
   for sanitized in(False,True):
    flags=['-std=c11','-O2','-Wall','-Wextra','-Wpedantic','-Werror',f'-DCHECKED={checked}','-I',w]
    if sanitized:flags+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
    binary=w/f'{cc}-{checked}-{sanitized}';run([cc,*flags,ROOT/'tests/protocol_bound_resources/host.c','-o',binary]);run([binary],cwd=w)
    runtime.append(dict(compiler=cc,checked=checked,sanitized=sanitized,scenarios=34))
 records=parse(plan.read_bytes());assert len(records)==3
 # A write automatically forgets currentness: bypassing refresh is rejected.
 current=source_from_record(records[0]);body=current[6][1]
 refresh=body[2][2];assert refresh[:2]==[Sym('Call'),Sym('refresh')]
 body[2][2]=refresh[2]
 stale=w/'stale.module';stale.write_text(module('bound_current_run',current))
 rejected=run([args.cesta,'protocol','check',stale,'Protocols'],ok=False)
 assert'unestablished'in rejected.stderr
 # Metadata names are checked rather than silently ignored.
 controls=[]
 changes=[]
 unknown=source_from_record(records[0]);next(c for c in unknown[3][1][7:]if c[0]=='Reads').append(Sym('absent'))
 changes.append(('unknown-instance',unknown,'undeclared resource instance'))
 no_owner=source_from_record(records[0]);checks_clause=next(c for c in no_owner[7:]if c[0]=='Checks');checks_clause[:]=[checks_clause[0],*(c for c in checks_clause[1:]if c[0]!='Own')]
 changes.append(('missing-owner-check',no_owner,'BoundOwner'))
 duplicate=source_from_record(records[0]);dependencies_clause=next(c for c in duplicate[7:]if c[0]=='Dependencies');dependencies_clause.append(copy.deepcopy(dependencies_clause[1]))
 changes.append(('duplicate-dependency',duplicate,'duplicate dependency'))
 for label,changed,needle in changes:
  path=w/(label+'.module');path.write_text(module('bound_current_run',changed))
  result=run([args.cesta,'protocol','check',path,'Protocols'],ok=False);assert needle in result.stderr,(label,result.stderr);controls.append(label)
 # A dishonest missing Writes declaration is caught by checked execution.
 dishonest=source_from_record(records[0]);mutate=next(h for h in dishonest[3][1:]if h[1]=='mutate')
 mutate[:]=[c for c in mutate if not(isinstance(c,list)and c and c[0]=='Writes')]
 dishonest_body=dishonest[6][1];dishonest_refresh=dishonest_body[2][2];assert dishonest_refresh[:2]==[Sym('Call'),Sym('refresh')]
 dishonest_body[2][2]=dishonest_refresh[2]
 path=w/'dishonest.module';path.write_text(module('bound_current_run',dishonest))
 run([args.cesta,'protocol','export',path,'Protocols','-o',w/'dishonest.h'])
 text=(ROOT/'tests/protocol_bound_resources/host.c').read_text().replace('#include "bound.h"','#include "dishonest.h"')
 text=text.replace(' if(bound_claim_run(&claim)!=bound_claim_run_done||claim.owned)return 5;','').replace(' if(bound_resume_run(&claim)!=bound_resume_run_done||claim.owned)return 6;','')
 (w/'dishonest.c').write_text(text);binary=w/'dishonest'
 run(['gcc','-std=c11','-O2','-DCHECKED=1','-I',w,w/'dishonest.c','-o',binary])
 result=subprocess.run([binary],cwd=w,capture_output=True);assert result.returncode!=0
 # Kernel-admit both the core lowering and resource metadata from the emitted plan.
 lines=['import '+MODEL,'import '+MODEL+'Instrumentation','import '+RESOURCE_MODEL,
        'set_option autoImplicit false','set_option Elab.async false','set_option maxRecDepth 8192',
        'open '+MODEL,'open '+RESOURCE_NS]
 for index,record in enumerate(records):
  name=str(record[1]);data,accesses,instances,dependencies,owners=base_data(record)
  base=core_source(name,data);bound=bound_literal(name,data,accesses,instances,dependencies,owners)
  target=core_body(record[7][1],True);section=record[8];itarget=section[2][1]
  lines += [f'def base{index} : Protocol := '+base,
            f'def bound{index} : BoundPlan := '+bound,
            f'def target{index} : CStmt := '+target,
            f'def plan{index} : Plan := ⟨base{index}, target{index}⟩',
            f'theorem coreAccepted{index} : checkPlan plan{index} = true := by decide',
            f'theorem boundAccepted{index} : checkBoundPlan bound{index} = true := by decide',
            f'def validators{index} : List Instrumentation.Validator := '+checks(data),
            f'def instrumented{index} : Instrumentation.IStmt := '+instrumented(itarget),
            f'def checked{index} : Instrumentation.RecordedPlan := ⟨base{index}, validators{index}, instrumented{index}⟩',
            f'theorem checkedAccepted{index} : Instrumentation.checkPlan checked{index} = true := by decide',
            f'#print axioms coreAccepted{index}',f'#print axioms boundAccepted{index}',f'#print axioms checkedAccepted{index}']
 lean=w/'Admission.lean';lean.write_text('\n'.join(lines)+'\n')
 result=subprocess.run(['lake','env','lean','-M','4096','-DwarningAsError=true',str(lean)],cwd=args.lean_root,capture_output=True,text=True)
 (w/'lean.log').write_text(result.stdout+result.stderr);assert result.returncode==0,(result.stdout[-7000:],result.stderr)
 assert'sorryAx'not in result.stdout and'native_decide'not in result.stdout
 summary=dict(runtime=runtime,plans=len(records),static_refusals=1+len(controls),dishonest_controls=1,
              compiler_sha256=hashlib.sha256(args.cesta.read_bytes()).hexdigest(),
              plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest())
 (w/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print('Bound protocols:',len(records),'actual plans admitted;',len(runtime)*34,'runtime scenarios;',summary['static_refusals'],'static refusals')

if __name__=='__main__':main()
