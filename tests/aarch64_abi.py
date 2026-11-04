"""AArch64 AAPCS64 ownership, layout, census and fail-closed target controls."""
from pathlib import Path
import subprocess,tempfile
ROOT=Path(__file__).resolve().parent.parent
BINARY=ROOT/'build/cesta'
def run(args,cwd,success=True):
    result=subprocess.run(list(map(str,args)),cwd=cwd,capture_output=True,text=True)
    assert (result.returncode==0)==success,(args,result.returncode,result.stdout[-1000:],result.stderr[-3000:])
    return result
def main():
    with tempfile.TemporaryDirectory(prefix='aarch64-abi-',dir=ROOT/'build') as name:
        work=Path(name);module=work/'unit.cesta';generated=work/'unit.c';plan=work/'unit.plan'
        module.write_text('''(cesta native-c/1 compact-c/2
  (unit
    (ownership (check-all))
    (native "struct Pair { char tag; long value; long double weight; };\nint score(struct Pair *p) { p->value += p->tag; return (int)p->value; }\n")))\n''')
        arm=run([BINARY,'export-c',module,'--cc','clang','--std=c11','--plan',plan,'-o',generated,'--','--target=aarch64-none-elf'],work)
        text=generated.read_text();assert 'AArch64 AAPCS64' in text and '(char)-1 > 0' in text
        assert '(function score ' in plan.read_text()
        run(['clang','--target=aarch64-none-elf','-std=c11','-fsyntax-only',generated],work)
        signed=run(['clang','--target=aarch64-none-elf','-fsigned-char','-std=c11','-fsyntax-only',generated],work,False)
        assert 'AArch64 AAPCS64' in signed.stderr
        run([BINARY,'export-c',module,'--cc','gcc','--std=c11','-o',work/'x86.c','--'],work)
        run(['gcc','-std=c11','-fsyntax-only',work/'x86.c'],work)
        unknown=run([BINARY,'export-c',module,'--cc','clang','--std=c11','-o',work/'riscv.c','--','--target=riscv64-none-elf'],work,False)
        assert 'no ABI model' in unknown.stderr
        plain=work/'plain.c';plain.write_text('struct Pair { char tag; long value; long double weight; }; int size(void){return sizeof(struct Pair);}\n')
        census=run([BINARY,'cc','--read-warnings','--cc','clang','--target=aarch64-none-elf','-Werror','-c',plain,'-o',work/'plain.o'],work)
        assert 'syntax and types admitted (aarch64-aapcs64-lp64)' in census.stderr
        tables=run([BINARY,'abi-tables','--abi=aarch64-aapcs64-lp64'],work)
        assert '(abi-tables aarch64-aapcs64-lp64' in tables.stdout
    print('AArch64 ABI: ownership/export/census admitted; signed-char and unknown-target controls refused')
if __name__=='__main__':main()
