"""Real ASan shadow changes, prefix preservation, refusal and disabled identity."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'build/region-debug'

SOURCE = r'''
#include "cesta_region.h"
#include <assert.h>
#include <string.h>

int evaluations;
void *side_effect(void) { ++evaluations;return NULL; }
int read_released_byte(volatile unsigned char *,size_t);

int main(int argc,char **argv) {
#if CESTA_REGION_DEBUG
    _Alignas(64) unsigned char storage[64];
    memset(storage,0xa5,sizeof storage);
    assert(CESTA_REGION_INITIALIZE(storage,64,16));
    for (unsigned i=0;i<64;i++)assert(__asan_address_is_poisoned(storage+i)==(i>=16));
    assert(CESTA_REGION_ACQUIRE(storage,64,16,32));
    memset(storage+16,0x5a,16);
    assert(CESTA_REGION_RESET(storage,64,32,16));
    for (unsigned i=0;i<64;i++)assert(__asan_address_is_poisoned(storage+i)==(i>=16));
    for (unsigned i=0;i<16;i++)assert(storage[i]==0xa5);
    assert(!CESTA_REGION_RESET(storage,64,16,15));
    assert(!CESTA_REGION_ACQUIRE(storage,64,16,SIZE_MAX));
    assert(!CESTA_REGION_RELEASE(NULL,64));
    assert(!CESTA_REGION_RELEASE((void *)(UINTPTR_MAX-15),64));
    assert(!CESTA_REGION_ACQUIRE(storage,64,32,16));
    assert(!CESTA_REGION_RESET(storage,64,16,32));
    for (unsigned i=0;i<64;i++)assert(__asan_address_is_poisoned(storage+i)==(i>=16));
    if (argc>1 && strcmp(argv[1],"suffix")==0) return read_released_byte(storage,16);
    assert(CESTA_REGION_RELEASE(storage,64));
    if (argc>1 && strcmp(argv[1],"released")==0) return read_released_byte(storage,0);
    assert(CESTA_REGION_INITIALIZE(storage,64,0));
    assert(CESTA_REGION_ACQUIRE(storage,64,0,16));
    memset(storage,0x33,16);
    for (unsigned i=0;i<16;i++)assert(storage[i]==0x33);
    assert(CESTA_REGION_INITIALIZE(storage,64,64));
    assert(CESTA_REGION_INITIALIZE(NULL,0,0));
    assert(CESTA_REGION_ACQUIRE(NULL,0,0,0));
    assert(CESTA_REGION_RESET(NULL,0,0,0));
    assert(CESTA_REGION_RELEASE(NULL,0));
#else
    (void)argc;(void)argv;
#if ERASE_HOOKS
    assert(true);
#else
    assert(CESTA_REGION_RELEASE(side_effect(),64));
#endif
    assert(!evaluations);
#endif
    return 0;
}
'''


def run(command):
    result = subprocess.run(list(map(str,command)),cwd=ROOT,capture_output=True)
    assert result.returncode == 0,(command,result.returncode,result.stderr.decode(errors='replace'))
    return result


def main():
    WORK.mkdir(parents=True,exist_ok=True)
    source=WORK/'control.c';source.write_text(SOURCE)
    reader=WORK/'reader.c'
    reader.write_text('#include <stddef.h>\nint read_released_byte(volatile unsigned char *p,size_t at){return p[at];}\n')
    identities=failures=0
    for compiler in ('gcc','clang'):
        for opt in ('-O0','-O2'):
            flags=[compiler,opt,'-g0','-std=c11','-Wall','-Wextra','-Werror','-Iadapters/asan']
            left=WORK/(compiler+opt+'.o');right=WORK/(compiler+opt+'-erased.o')
            run([*flags,'-DERASE_HOOKS=0','-c',source,'-o',left])
            run([*flags,'-DERASE_HOOKS=1','-c',source,'-o',right])
            assert left.read_bytes()==right.read_bytes(),(compiler,opt)
            identities+=1
        binary=WORK/(compiler+'-asan')
        flags=[compiler,'-O1','-g','-std=c11','-Wall','-Wextra','-Werror','-Iadapters/asan',
               '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer']
        run([*flags,source,reader,'-o',binary]);run([binary])
        for fault in ('suffix','released'):
            result=subprocess.run([str(binary),fault],capture_output=True)
            assert result.returncode != 0 and b'use-after-poison' in result.stderr,(compiler,fault,result.stderr)
            failures+=1
    (WORK/'summary.json').write_text(json.dumps({'off_object_identities':identities,
        'asan_compilers':['gcc','clang'],'use_after_poison_controls':failures,
        'prefix_refusal_reuse_zero_length':True},indent=2)+'\n')
    print(f'region debug: {identities} disabled object identities, GCC/Clang shadow checks, '
          f'{failures} use-after-poison controls; prefix/refusal/reuse/zero lengths pass',flush=True)


if __name__=='__main__':main()
