"""Actual-header ABI binding, relocation and stale-interface controls."""

import hashlib
import json
import random
import subprocess
import tempfile
from pathlib import Path

from checked_spans import BINARY, ROOT, run
from oracles.pro_compact_cesta import Text, parse


def digest_controls(work):
    source = work/'digest.c'
    source.write_text('''#include "digest.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc,char **argv){
  size_t chunk=argc==2?(size_t)strtoul(argv[1],0,10):1;
  if(!chunk||chunk>4096)return 1;
  CeDigest d;ce_digest_init(&d);unsigned char b[4096];size_t n;
  while((n=fread(b,1,chunk,stdin))!=0)if(!ce_digest_update(&d,b,n))return 2;
  if(ferror(stdin))return 3;
  char hex[65];ce_digest_finish(&d,hex);puts(hex);
  ce_digest_init(&d);d.bytes=UINT64_MAX/8;
  if(ce_digest_update(&d,b,1))return 4;
  return 0;
}
''')
    rng = random.Random(261109)
    cases = [b'',b'abc',b'a'*1000000]
    cases += [rng.randbytes(n) for n in (1,31,55,56,57,63,64,65,119,120,127,128,129,4095,4096,4097)]
    for cc in ('gcc','clang'):
        binary=work/f'digest-{cc}'
        run([cc,'-std=c11','-Wall','-Wextra','-Werror','-O2','-fsanitize=address,undefined',
             '-fno-pie','-no-pie','-I',ROOT/'src',source,ROOT/'src/digest.c','-o',binary])
        for data in cases:
            for chunk in (1,7,64,4096):
                result=subprocess.run([binary,str(chunk)],input=data,capture_output=True)
                assert result.returncode==0,(cc,len(data),chunk,result.stderr)
                assert result.stdout.strip().decode()==hashlib.sha256(data).hexdigest()
    return len(cases)*8


HEADER='''#ifndef TEST_INTERFACE_H
#define TEST_INTERFACE_H
#include <stddef.h>
#include <stdint.h>
typedef struct {const uint32_t *data;size_t len;} Words;
typedef struct {uint8_t tag;uint32_t value;} Item;
uint32_t fetch(const Item *item);
#endif
'''
BODY='''#include "interface.h"
uint32_t fetch(const Item *item){return item->value;}
'''


def document(pin=None,body=BODY):
    return '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) '+\
        '(view Words data len read u32) '+\
        ('(abi-fingerprint '+json.dumps(pin)+') ' if pin else '')+\
        ') (native '+json.dumps(body)+')))'


def export(directory,cc,flags=()):
    source=directory/'unit.cesta';plan=directory/'unit.plan'
    result=run([BINARY,'export-c',source,'--cc',cc,'--plan',plan,'-o',directory/'unit.c','--',*flags])
    record,=parse(plan.read_bytes())
    abi,=[part for part in record[2:] if part[0]=='abi']
    assert isinstance(abi[2][1],Text)
    return abi[2][1].data.decode(),plan.read_bytes(),result


def main():
    work=Path(tempfile.mkdtemp(prefix='ownership-abi-',dir=ROOT/'build'))
    digests=digest_controls(work)
    for cc in ('gcc','clang'):
        original,moved=(work/cc/workname for workname in ('original','relocated'))
        for directory in (original,moved):
            directory.mkdir(parents=True)
            (directory/'interface.h').write_text(HEADER)
            (directory/'unit.cesta').write_text(document())
        expected,first,_=export(original,cc)
        relocated,second,_=export(moved,cc)
        assert expected==relocated and first==second,'ABI record depends on build directory'
        for directory in (original,moved):(directory/'unit.cesta').write_text(document(expected))
        pinned,_,_=export(moved,cc)
        assert pinned==expected,'pin changes its own ABI fingerprint'
        run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only',moved/'unit.cesta'])
        # A unit written over several lines is laid out differently by export
        # and by compilation; a pin taken from the exported plan holds for both.
        spread=work/cc/'spread';spread.mkdir();(spread/'interface.h').write_text(HEADER)
        body=BODY.replace('{return item->value;}','{\n    uint32_t value = item->value;\n\n    return value;\n}')
        lines=lambda text:text.replace('(unit (ownership','(unit\n\n  (ownership').replace(') (native ',')\n\n\n  (native ')
        assert body!=BODY and lines(document(body=body)).count('\n')==5
        (spread/'unit.cesta').write_text(lines(document(body=body)))
        laid,_,_=export(spread,cc)
        (spread/'unit.cesta').write_text(lines(document(laid,body)))
        assert export(spread,cc)[0]==laid
        run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only',spread/'unit.cesta'])
        for header in (HEADER+'/* API edition changed. */\n',
                       HEADER.replace('uint8_t tag','uint64_t tag'),
                       HEADER.replace('uint32_t value','uint16_t value'),
                       HEADER.replace('fetch(const Item *item)','fetch(Item *item)'),
                       HEADER.replace('const uint32_t *data','const uint16_t *data')):
            (moved/'interface.h').write_text(header)
            run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only',moved/'unit.cesta'],accepted=False)
        (moved/'interface.h').write_text(HEADER)
        run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only','-funsigned-char',moved/'unit.cesta'],accepted=False)
        # The identical C program remains compilable without enrichment.
        plain=moved/'plain.c';plain.write_text(BODY)
        run([BINARY,'cc','--cc',cc,'-std=c11','-fsyntax-only',plain])
        for invalid in ('0'*63,'G'*64,expected+'0'):
            (moved/'unit.cesta').write_text(document(invalid))
            run([BINARY,'cc','--cc',cc,'-fsyntax-only',moved/'unit.cesta'],accepted=False)
    arm=work/'arm';arm.mkdir()
    # Freestanding header lets Clang select AAPCS64 without a target sysroot.
    (arm/'interface.h').write_text(HEADER.replace('#include <stddef.h>','typedef __SIZE_TYPE__ size_t;').replace(
        '#include <stdint.h>','typedef unsigned char uint8_t;typedef unsigned int uint32_t;'))
    (arm/'unit.cesta').write_text(document())
    x86,_,_=export(arm,'clang')
    aarch64,arm_plan,_=export(arm,'clang',('--target=aarch64-none-elf',))
    assert x86!=aarch64 and b'aapcs64' in arm_plan
    big_endian,big_plan,_=export(arm,'clang',('--target=aarch64_be-none-elf',))
    assert big_endian!=aarch64 and b'__ORDER_BIG_ENDIAN__' in big_plan
    (arm/'unit.cesta').write_text(document(aarch64))
    run([BINARY,'cc','--cc','clang','--target=aarch64-none-elf','-fsyntax-only',arm/'unit.cesta'])
    (arm/'unit.cesta').write_text(document(x86))
    run([BINARY,'cc','--cc','clang','--target=aarch64-none-elf','-fsyntax-only',arm/'unit.cesta'],accepted=False)
    print(f'ABI: GCC/Clang actual headers; relocated plans identical; stale header/type/target pins refused; AAPCS64; {digests} SHA-256 controls')


if __name__=='__main__':main()
