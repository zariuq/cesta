"""Two-phase typed span firing, including legal destination/input overlap."""

import argparse
from pathlib import Path
import subprocess
import tempfile

from checked_spans import BINARY, ROOT, run

HOST = r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "flat.h"

#ifdef WITH_ASSEMBLY
extern uint64_t flat_fire(const uint32_t *, const uint32_t *, const uint32_t *,
                         uint64_t, const uint32_t *, uint64_t, uint32_t *, uint32_t *);
#endif
void flat_span_failure(void) { _Exit(73); }
static uint32_t seed = 314159;
static uint32_t next(void) { seed = seed * UINT32_C(1664525) + UINT32_C(1013904223); return seed; }
static size_t offset(const uint32_t *rows, uint32_t selector) {
    return (uint32_t)(rows[selector & 255] + ((selector >> 8) & 255));
}
static bool reference(uint32_t *fields, const uint32_t *rows,
                      const uint32_t *guards, size_t ng, const uint32_t *makes,
                      size_t nm, uint32_t *dest, uint32_t *scratch) {
    for(size_t i=0;i<ng;++i)
        if(fields[offset(rows,guards[i])] != fields[offset(rows,guards[i]>>16)]) return false;
    for(size_t i=0;i<nm;++i) scratch[i]=fields[offset(rows,makes[i])];
    for(size_t i=0;i<nm;++i) dest[i]=scratch[i];
    return true;
}
static bool actual(uint32_t *fields, const uint32_t *rows, const uint32_t *guards,
                   size_t ng, const uint32_t *makes, size_t nm, uint32_t *dest,
                   uint32_t *scratch) {
    bool ok=flat_span_prepare((cesta_read_u32_span){fields,64},(cesta_read_u32_span){rows,4},
        (cesta_read_u32_span){guards,ng},(cesta_read_u32_span){makes,nm},(cesta_write_u32_span){scratch,16});
    if(ok)flat_span_publish((cesta_read_u32_span){scratch,16},(cesta_write_u32_span){dest,16},nm);
    return ok;
}
int main(int argc,char **argv) {
    (void)argv;
    if(argc>1) {
        uint32_t fields[64]={0},rows[4]={64},g[1]={0},scratch[16]={0};
        return actual(fields,rows,g,1,g,0,fields,scratch);
    }
    for(size_t test=0;test<4096;++test) {
        uint32_t a[64], b[64], rows[4]={0,16,32,48}, guards[16], makes[16], sa[16], sb[16];
        for(size_t i=0;i<64;++i) a[i]=b[i]=next()%7;
        for(size_t i=0;i<16;++i) {
            uint32_t first=(next()%4) | ((next()%16)<<8);
            uint32_t second=(next()%4) | ((next()%16)<<8);
            guards[i]=first | ((test%2 ? first : second)<<16);
            makes[i]=(next()%4) | ((next()%16)<<8);
            sa[i]=sb[i]=next();
        }
        size_t ng=test%17,nm=(test/17)%17,dest=(test%3)*16;
        bool expected=reference(b,rows,guards,ng,makes,nm,b+dest,sb);
        bool observed=actual(a,rows,guards,ng,makes,nm,a+dest,sa);
        assert(observed==expected);
        assert(!memcmp(a,b,sizeof a) && !memcmp(sa,sb,sizeof sa));
#ifdef WITH_ASSEMBLY
        /* Recreate exactly the same initial state to compare the reference bytes. */
        uint32_t c[64], d[64], sc[16], sd[16];
        for(size_t i=0;i<64;++i)c[i]=d[i]=next()%7;
        for(size_t i=0;i<16;++i)sc[i]=sd[i]=next();
        uint64_t ar=flat_fire(c,rows,guards,ng,makes,nm,c+dest,sc);
        bool cr=actual(d,rows,guards,ng,makes,nm,d+dest,sd);
        assert(ar==(uint64_t)cr && !memcmp(c,d,sizeof c) && !memcmp(sc,sd,sizeof sc));
#endif
    }
    uint32_t fields[64]={123,456},rows[4]={UINT32_MAX,0},guards[1]={256},makes[1]={256};
    uint32_t scratch[16]={0};
    assert(actual(fields,rows,guards,0,makes,1,fields+1,scratch));
    assert(fields[1]==123 && scratch[0]==123);
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--assembly', type=Path)
    args = p.parse_args()
    work = Path(tempfile.mkdtemp(prefix='flat-spans-', dir=ROOT/'build'))
    source = ROOT/'checked/examples/FlatFire.cesta'
    header, exported, host = work/'flat.h', work/'flat.c', work/'host.c'
    run([BINARY,'checked','header',source,'-o',header])
    run([BINARY,'export-c',source,'-o',exported,'--plan',work/'flat.plan'])
    host.write_text(HOST)
    for cc in ('gcc','clang'):
        for sanitize in (False,True):
            binary = work/f'{cc}-{sanitize}'
            flags = ['-std=c11','-Wall','-Wextra','-Werror','-O2','-I',work]
            if sanitize: flags += ['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
            inputs = [host,exported]
            if args.assembly: flags += ['-DWITH_ASSEMBLY']; inputs += [args.assembly.resolve()]
            run([cc,*flags,*inputs,'-o',binary]); run([binary])
            assert run([binary,'bounds'],accepted=False).returncode == 73
    print('flat spans: 4096 overlap/refusal scenarios per build; modular offsets; 4 GCC/Clang/sanitizer builds'
          + ('; reference assembly agrees' if args.assembly else ''))


if __name__ == '__main__': main()
