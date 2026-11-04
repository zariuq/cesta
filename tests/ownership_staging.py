"""Check that function certificates can be resumed into module admission."""
import argparse
import json
from pathlib import Path
import subprocess

import ownership_admission as native

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cesta',type=Path,default=ROOT/'build/cesta')
    parser.add_argument('--lean-root',type=Path,default=ROOT.parent/'Mettapedia/lean/mettapedia')
    parser.add_argument('--output',type=Path,default=ROOT/'build/ownership-staging')
    args=parser.parse_args();work=args.output.resolve();work.mkdir(parents=True,exist_ok=True)
    source=work/'staging.cesta'
    source.write_text('''(cesta native-c/1 compact-c/2
      (unit (ownership (check-all))
        (native "#include <stdint.h>
uint32_t read_cell(const uint32_t *cell) { return *cell; }
uint32_t read_local(uint32_t value) { uint32_t local=value; return read_cell(&local); }
")))
''')
    plan=work/'staging.plan'
    subprocess.run([str(args.cesta.resolve()),'export-c',str(source),'--std=c11',
                    '-o',str(work/'staging.c'),'--plan',str(plan)],check=True,cwd=ROOT)
    record,=native.parse(plan.read_bytes());module=work/'Staging.lean'
    before=module.read_bytes()if module.exists()else None
    staged=native.admit_record(record,module,args.lean_root.resolve(),assemble=False)
    assert staged['functions']==2 and staged['module_admitted']is False
    assert (module.read_bytes()if module.exists()else None)==before,'parts phase wrote a module certificate'
    complete=native.admit_record(record,module,args.lean_root.resolve())
    assert complete['module_admitted']is True and complete['functions']==2
    assert complete['cached_parts']>=3,'resumption did not reuse its checked function certificates'
    assert complete['plan_sha256']==staged['plan_sha256']
    assert 'theorem wholeModule_admitted'in module.read_text()
    (work/'summary.json').write_text(json.dumps(dict(staged=staged,complete=complete),indent=2)+'\n')
    print('ownership staging: two actual function plans; module deferred explicitly, then admitted using checked cache')


if __name__=='__main__':main()
