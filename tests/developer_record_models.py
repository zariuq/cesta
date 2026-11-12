"""Development-only replay of record declarations; never called by Cesta users."""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess

from oracles.pro_compact_cesta import parse, Text
from lean_contracts import code_without_comments, named_theorems

ROOT=Path(__file__).resolve().parent.parent
MODULES=['Mettapedia.GSLT.LanguageDef.RecordVisitorContracts',
         'Mettapedia.GSLT.LanguageDef.RegionDebugContracts']
NAMES={b'':''}


def raw(value):
    return value.data if isinstance(value,Text) else str(value).encode()


def string(value):
    # Only nominal equality and emptiness are observed by this model. Keep
    # a lossless name table instead of reducing long byte strings in Lean.
    data=raw(value)
    if data not in NAMES:NAMES[data]='n'+str(len(NAMES))
    return json.dumps(NAMES[data])


def parts(value,name):
    return [part for part in value if isinstance(part,list) and part and str(part[0])==name]


def policy(p):
    return ('⟨'+','.join([string(p[1]),string(p[2]),str(p[3]=='write').lower(),
        str(p[4]=='address').lower(),str(p[5]=='counted').lower(),string(p[6])])+'⟩')


def field(f):
    access=parts(f,'access')[0]
    visitor='none' if f[4]==[] else '(some '+string(f[4])+')'
    return ('⟨'+','.join([string(f[1]),'.'+str(f[2]),string(f[3]),str(access[1]=='write').lower(),
        visitor,str(bool(raw(f[5]))).lower(),str(bool(parts(f,'count'))).lower()])+'⟩')


def record(r):
    declarations=parts(r,'declared')[0][1:]
    classified=parts(r,'classified')[0][1:]
    ds='['+','.join('⟨'+string(d[1])+','+str(d[2]=='reference').lower()+'⟩' for d in declarations)+']'
    fs='['+','.join(field(f) for f in classified)+']'
    return '⟨'+ds+','+fs+','+str(r[2]=='write').lower()+'⟩'


def mutations(r):
    classified=parts(r,'classified')[0]
    references=[i for i,f in enumerate(classified[1:],1) if f[2]!='value']
    result=[]
    if references:
        index=references[0]
        changed=copy.deepcopy(r);parts(changed,'classified')[0].pop(index)
        result.append(('missing',changed))
        changed=copy.deepcopy(r);parts(changed,'classified')[0].append(copy.deepcopy(classified[index]))
        result.append(('duplicate',changed))
        changed=copy.deepcopy(r);parts(changed,'classified')[0][index][3]=Text(b'unknown-owner')
        result.append(('owner',changed))
        changed=copy.deepcopy(r);parts(changed,'classified')[0][index][4]=Text(b'unknown-visitor')
        result.append(('visitor',changed))
    changed=copy.deepcopy(r);parts(changed,'declared')[0].append(['field',Text(b'missing-root'),'reference',Text(b'void *')])
    result.append(('new_root',changed))
    return result


def run(command,cwd,log,environment=None):
    result=subprocess.run(list(map(str,command)),cwd=cwd,env=environment,capture_output=True)
    log.write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,(command,result.stderr.decode(errors='replace'),str(log))
    return result.stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facts',type=Path,action='append',required=True)
    parser.add_argument('--lean-project',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    parser.add_argument('--lean-memory-mb',type=int)
    parser.add_argument('--work',type=Path,default=ROOT/'build/developer-record-models')
    args=parser.parse_args();project=args.lean_project.resolve();work=args.work.resolve()
    NAMES.clear();NAMES[b'']=''
    work.mkdir(parents=True,exist_ok=True)
    lean=['lake','env','lean','-DwarningAsError=true']
    if args.lean_memory_mb:lean+=['-M',str(args.lean_memory_mb)]
    theorems=[];sources={}
    for module in MODULES:
        source=project/(module.replace('.','/')+'.lean')
        code=code_without_comments(source.read_text())
        assert not re.search(r'\b(?:sorry|axiom|native_decide|theorem_wanted)\b',code)
        assert '_wanted' not in code
        sources[module]=hashlib.sha256(source.read_bytes()).hexdigest()
        theorems+=named_theorems(code)
        artifact=project/'.lake/build/lib/lean'/(module.replace('.','/')+'.olean')
        artifact.parent.mkdir(parents=True,exist_ok=True)
        run([*lean,'-o',artifact,source],project,work/(module.split('.')[-1]+'.log'))
    lines=['import '+m for m in MODULES]
    lines+=['open Mettapedia.GSLT.LanguageDef.RecordVisitors',
            'set_option maxRecDepth 8192', 'set_option maxHeartbeats 4000000']
    positives=negatives=0
    for input_index,path in enumerate(args.facts):
        forms=parse(path.read_bytes())
        assert len(forms)==1
        facts=forms[0]
        assert str(facts[0])=='cesta' and str(facts[1])=='record-visitors-facts/1'
        owners=parts(facts,'owners')[0][1:];policies=parts(facts,'visitor')
        env='environment'+str(input_index)
        lines.append('def '+env+' : Environment := ⟨['+','.join(map(string,owners))+'],['+','.join(map(policy,policies))+']⟩')
        for r in parts(facts,'record'):
            name='real_record_'+str(positives)
            datum=name+'_data'
            lines.append('def '+datum+' : Record := '+record(r))
            lines.append('theorem '+name+' : checkRecord '+env+' '+datum+' = true := by decide')
            positives+=1
            for kind,changed in mutations(r):
                datum='refused_'+str(negatives)+'_'+kind
                lines.append('def '+datum+'_data : Record := '+record(changed))
                lines.append('theorem '+datum+' : checkRecord '+env+' '+datum+'_data = false := by decide')
                negatives+=1
    for theorem in theorems:lines.append('#print axioms '+theorem)
    source=work/'RecordsActual.lean';source.write_text('\n'.join(lines)+'\n')
    (work/'names.json').write_text(json.dumps({name:data.hex() for data,name in NAMES.items()},indent=2)+'\n')
    output=run([*lean,'--root='+str(work),source],project,work/'actual.log')
    assert b'sorryAx' not in output and b'error:' not in output
    allowed={'propext','Quot.sound','Classical.choice'}
    for group in re.findall(rb'depends on axioms:\s*\[([^\]]*)\]',output):
        axioms={x.strip().decode() for x in group.split(b',') if x.strip()}
        assert axioms<=allowed,axioms
    summary={'scope':'Development laws for Cesta declarations and relative region effects; not C execution or a GC correctness theorem.',
        'modules':sources,'generic_theorems':len(theorems),'actual_records':positives,'mutated_record_refusals':negatives}
    (work/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(f'development record models: {len(MODULES)} modules, {len(theorems)} generic theorem audits, '
          f'{positives} actual records, {negatives} mutated refusals',flush=True)


if __name__=='__main__':main()
