"""Kernel-admit the actual bounded protocol target and reject changed plans."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from protocols import CESTA, ROOT, run
from protocol_admission import string, strings, reason, exit_facts
from oracles.pro_compact_cesta import Sym, parse

MODEL = 'Mettapedia.GSLT.LanguageDef.ExecutableProtocolLoops'
NS = 'Mettapedia.GSLT.LanguageDef.ExecutableProtocol.Loops'
CHECKED_MODEL = 'Mettapedia.GSLT.LanguageDef.ExecutableProtocolLoopChecks'

def bound(value,target=False):
    if isinstance(value,list):
        assert len(value)==2 and value[0] == ('Field' if target else 'Limit'),value
        return '(.field '+string(value[1])+')'
    return '(.literal '+str(int(value))+')'

def body(value,target=False):
    kind=str(value[0])
    if kind=='Continue':
        assert len(value)==1
        return '.cont'
    if kind=='Return': return '(.ret .'+str(value[1])+')'
    if kind in ('Loop','While'):
        assert kind==('While' if target else 'Loop') and len(value)==4
        raw=value[1]
        if target and raw[0]=='Literal': raw=raw[1]
        return '(.loop '+bound(raw,target)+' '+body(value[2],target)+' '+body(value[3],target)+')'
    constructors=({'IfCall':'ifCall','VoidCall':'voidCall'} if target else
                  {'Call':'call','Acquire':'acquire','Release':'release','Transfer':'transfer'})
    return '(.'+constructors[kind]+' '+string(value[1])+''.join(' '+body(v,target) for v in value[2:])+')'

def source(name,parts,exits=None):
    hooks=[]
    for h in parts[1][1:]:
        assert h[0]=='Hook' and len(h) in (7,8)
        if len(h)==8: assert h[7][0]=='Invalidates'
        hooks.append('⟨'+', '.join([string(h[1]),string(h[2]),'.'+str(h[3]),
                                   strings(h[4][1:]),strings(h[5][1:]),strings(h[6][1:]),
                                   strings(h[7][1:] if len(h)==8 else [])])+'⟩')
    resources=[]
    for r in parts[2][1:]:
        assert r[0]=='Resource' and len(r) in (5,7)
        resources.append('⟨'+', '.join([*(string(v) for v in r[1:5]),
                           strings(r[5][1:] if len(r)==7 else []),
                           strings(r[6][1:] if len(r)==7 else [])])+'⟩')
    return '⟨'+', '.join([string(name),string(parts[0][1]),'['+', '.join(hooks)+']',
                          '['+', '.join(resources)+']',strings(parts[3][1][1:]),
                          strings(parts[3][2][1:]),body(parts[4][1]),exit_facts(exits)])+'⟩'

def checked(value):
    kind=str(value[0])
    if kind=='Validate':
        assert len(value)==6 and value[1] in ('fact','own') and value[4] in ('true','false')
        return '(.validate ⟨.'+str(value[1])+', '+string(value[2])+', '+string(value[3])+', '+str(value[4])+'⟩ '+checked(value[5])+')'
    if kind=='Tick':return '(.tick '+reason(value[1])+' '+checked(value[2])+')'
    if kind=='Continue':return '.cont'
    if kind=='Return':return '(.ret .'+str(value[1])+')'
    if kind=='While':
        limit=value[1][1] if value[1][0]=='Literal' else value[1]
        return '(.loop '+bound(limit,True)+' '+checked(value[2])+' '+checked(value[3])+')'
    return '(.'+{'IfCall':'ifCall','VoidCall':'voidCall'}[kind]+' '+string(value[1])+''.join(' '+checked(child) for child in value[2:])+')'

def checked_mutations(tree):
    result=[]
    def visit(value,path):
        kind=value[0]
        if kind=='Validate':
            for mode in ('drop','flip','predicate'):
                changed=copy.deepcopy(tree); cursor=changed
                for index in path[:-1]:cursor=cursor[index]
                node=cursor[path[-1]] if path else changed
                if mode=='drop':node=copy.deepcopy(node[5])
                elif mode=='flip':node[4]=Sym('false' if node[4]=='true' else 'true')
                else:node[3]=Sym('forged_validator')
                if path:cursor[path[-1]]=node
                else:changed=node
                result.append(changed)
            visit(value[5],path+[5])
        elif kind=='Tick':visit(value[2],path+[2])
        elif kind in ('While','IfCall','VoidCall'):
            for index in range(2,len(value)):visit(value[index],path+[index])
    visit(tree,[])
    return result

def mutations(tree):
    result=[]
    def change(path,edit):
        copy_tree=copy.deepcopy(tree)
        cursor=copy_tree
        for i in path[:-1]: cursor=cursor[i]
        if path: cursor[path[-1]]=edit(cursor[path[-1]])
        else: copy_tree=edit(copy_tree)
        result.append(copy_tree)
    def visit(value,path):
        kind=value[0]
        if kind=='Continue':
            change(path,lambda _:[Sym('Return'),Sym('done')]);return
        if kind=='Return':
            change(path,lambda v:[v[0],Sym('raised' if v[1]!='raised' else 'done')]);return
        if kind=='While':
            def wrong_limit(v):
                v[1]=[Sym('Literal'),Sym('17')]; return v
            change(path,wrong_limit)
            change(path,lambda v:v[3])
        else:
            def wrong_name(v):v[1]=Sym('forged_call');return v
            change(path,wrong_name)
        for i in range(2,len(value)):visit(value[i],path+[i])
    visit(tree,[])
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cesta',type=Path,default=CESTA)
    parser.add_argument('--lean-project',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    parser.add_argument('--module',type=Path,action='append')
    parser.add_argument('--lean-memory-mb',type=int)
    args=parser.parse_args()
    project=args.lean_project.resolve()
    modules=args.module or [ROOT/'protocols/Wait.module']
    work=Path(tempfile.mkdtemp(prefix='protocol-loop-admission-',dir=ROOT/'build'))
    lines=['import '+MODEL,'import '+CHECKED_MODEL,'set_option autoImplicit false','set_option Elab.async false',
           'set_option maxRecDepth 8192','open '+NS]
    count=refused=0
    for path in modules:
        plan=work/(path.stem+'.plan')
        header=work/(path.stem+'.h')
        run([args.cesta,'protocol','export',path,'Protocols','-o',header,'--plan',plan])
        for recorded in parse(plan.read_bytes()):
            assert len(recorded) in (9,10) and recorded[0]=='cesta-protocol-plan/1'
            exits=recorded[9] if len(recorded)==10 else None
            name=recorded[1]; parts=recorded[2:7]; target=recorded[7][1]
            key='recorded'+str(count)
            lines.append('def '+key+' : Plan := ⟨'+source(name,parts,exits)+', '+body(target,True)+'⟩')
            accepted='accepted'+str(count)
            lines.append('theorem '+accepted+' : checkPlan '+key+' = true := by decide')
            for mutant in mutations(target):
                lines.append('example : checkPlan { '+key+' with target := '+body(mutant,True)+' } = false :=\n  changed_target_refused ((checkPlan_iff _).mp '+accepted+') _ (by decide)')
                refused+=1
            instrumentation=recorded[8]
            assert instrumentation[0]=='Instrumentation'
            validators='['+', '.join('⟨.'+str(v[0]).lower()+', '+string(v[1])+', '+string(v[2])+'⟩'
                                      for v in instrumentation[1][1:])+']'
            available=instrumentation[2][0]=='Target'
            written='some '+checked(instrumentation[2][1]) if available else 'none'
            checked_key='checked'+str(count)
            lines.append('def '+checked_key+' : Instrumentation.CheckedPlan := ⟨'+key+', '+validators+', '+written+'⟩')
            checked_accepted='checkedAccepted'+str(count)
            lines.append('theorem '+checked_accepted+' : Instrumentation.checkCheckedPlan '+checked_key+' = true := by decide')
            if available:
                for mutant in checked_mutations(instrumentation[2][1]):
                    lines.append('example : Instrumentation.checkCheckedPlan { '+checked_key+' with instrumented := some '+checked(mutant)+' } = false :=\n  Instrumentation.changed_instrumentation_refused ((Instrumentation.checkCheckedPlan_iff _).mp '+checked_accepted+') _ (by decide)')
                    refused+=1
            count+=1
    for theorem in ['checkBody_iff','checkPlan_iff','lower_eval_exact','continue_preserves_owners',
                    'return_discharges','sourceLoop_turns_le','targetLoop_turns_le','sourceLoop_retains_charges','changed_target_refused']:
        lines.append('#print axioms '+NS+'.'+theorem)
    for theorem in ['instrumentBody_erase','Run.erases','failed_validation_stops','checkCheckedPlan_iff',
                    'CheckedPlanValid.success_exact','changed_instrumentation_refused']:
        lines.append('#print axioms '+NS+'.Instrumentation.'+theorem)
    lean=work/'Admission.lean';lean.write_text('\n'.join(lines)+'\n')
    command=['lake','env','lean']
    if args.lean_memory_mb is not None:
        if args.lean_memory_mb <= 0: parser.error('--lean-memory-mb must be positive')
        command += ['-M',str(args.lean_memory_mb)]
    result=subprocess.run([*command,str(lean)],cwd=project,capture_output=True,text=True)
    (work/'lean.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,(work,result.stdout[-6000:],result.stderr[-3000:])
    assert 'sorryAx' not in result.stdout and 'native_decide' not in result.stdout
    print('loop admission:',count,'actual targets admitted;',refused,'mutated targets refused')
    (work/'summary.json').write_text(json.dumps({'plans':count,'refused':refused,
        'compiler_sha256':hashlib.sha256(args.cesta.read_bytes()).hexdigest()},indent=2)+'\n')

if __name__=='__main__':main()
