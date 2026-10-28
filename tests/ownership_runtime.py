"""Exercise real heap transfers, denied allocations and annotation erasure."""

import hashlib
import json
from pathlib import Path
import tempfile

from ownership_core import BINARY, POSITIVE, ROOT, document, run


PROVIDER = r'''
#include <stdlib.h>
#include <assert.h>
static unsigned allocations, releases, live;
void *reserve(unsigned long n) {
#if FAIL_ALLOCATION
    (void)n;
    return NULL;
#else
    void *p = malloc(n);
    if (p) { ++allocations; ++live; }
    return p;
#endif
}
void dispose(void *p) {
    if (p) { assert(live == 1); --live; ++releases; }
    free(p);
}
extern int f(void);
int main(void) {
    int result = f();
#if FAIL_ALLOCATION
    assert(result == 0 && allocations == 0 && releases == 0 && live == 0);
#else
    assert(result == EXPECTED && allocations == 1 && releases == 1 && live == 0);
#endif
    return 0;
}
'''


def checked(command):
    result = run(command)
    assert result.returncode == 0,(command,result.stdout,result.stderr)
    return result


def main():
    records = []
    with tempfile.TemporaryDirectory(prefix="ownership-runtime-",dir=ROOT/"build") as temporary:
        directory = Path(temporary)
        provider = directory/"provider.c"; provider.write_text(PROVIDER)
        for name, expected in (("heap_move",7),("heap_borrow",7),("owned_argument",1),("owned_result",7)):
            source, contract = POSITIVE[name]
            case = directory/name; case.mkdir()
            authored = case/"unit.cesta"; authored.write_text(document(source,contract))
            plain = case/"plain"; plain.mkdir()
            exported = plain/"unit.c"
            baseline = case/"unit.c"; baseline.write_text(source)
            for compiler in ("gcc","clang"):
                checked([BINARY,"export-c",authored,"--cc",compiler,"--std=c11","-o",exported])
                for denied in (0,1):
                    for sanitize in (False,True):
                        flags = ["-std=c11","-O2","-g0","-Werror",f"-DFAIL_ALLOCATION={denied}",f"-DEXPECTED={expected}"]
                        if sanitize:
                            flags += ["-fsanitize=address,undefined","-fno-sanitize-recover=all"]
                        binaries = []
                        for route, path in (("authored",authored),("exported",exported),("erased",baseline)):
                            binary = case/f"{compiler}-{denied}-{int(sanitize)}-{route}"
                            command = [BINARY,"cc","--cc",compiler] if route == "authored" else [compiler]
                            checked([*command,*flags,path,provider,"-o",binary])
                            checked([binary]); binaries.append(binary)
                        if not sanitize:
                            digests = [hashlib.sha256(p.read_bytes()).hexdigest() for p in binaries]
                            assert len(set(digests)) == 1,(name,compiler,denied,digests)
                        records.append({"case":name,"compiler":compiler,"denied":bool(denied),"sanitized":sanitize})
        for authored in sorted((ROOT/"ownership/examples").glob("*.cesta")):
            case = directory/authored.stem; case.mkdir()
            exported = case/(authored.stem+".c")
            for compiler in ("gcc","clang"):
                checked([BINARY,"export-c",authored,"--cc",compiler,"--std=c11","-o",exported])
                binaries = []
                for route, source in (("authored",authored),("exported",exported)):
                    binary = case/(compiler+"-"+route)
                    command = [BINARY,"cc","--cc",compiler] if route == "authored" else [compiler]
                    checked([*command,"-std=c11","-O2","-g0",source,"-o",binary])
                    checked([binary]); binaries.append(binary)
                assert binaries[0].read_bytes() == binaries[1].read_bytes(),(authored.name,compiler)
    (ROOT/"build/ownership-runtime.json").write_text(json.dumps(records,indent=2)+"\n")
    print(f"ownership runtime: {len(records)*3} heap executions and both structured examples; allocation success/failure, GCC/Clang, sanitizers; authored/exported/erased binaries agree")


if __name__ == "__main__":
    main()
