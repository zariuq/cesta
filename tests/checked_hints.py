"""Check compiler hints against the existing ownership and failure-path oracle."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from checked_core import BINARY, MORE, ROOT, RUNTIME, run


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    work = Path(tempfile.mkdtemp(prefix="checked-hints-", dir=ROOT / "build"))
    names = ("ByteViews", "Owners", "Extra")
    sources = [ROOT / "checked/examples/ByteViews.cesta",
               ROOT / "checked/examples/Owners.cesta", work / "Extra.cesta"]
    sources[-1].write_text(MORE)
    for name, source in zip(names, sources):
        run([BINARY, "checked", "header", source, "-o", work / (name + ".h")])
        run([BINARY, "export-c", source, "-o", work / (name + ".c"), "--"])
    exported = [work / (name + ".c") for name in names]
    (work / "runtime.c").write_text(RUNTIME)
    # The allocator is defined in another unit. The size must come from the
    # declaration emitted by Cesta, rather than interprocedural body inference.
    (work / "extent.c").write_text(r'''
#include "Owners.h"
#include <stdlib.h>
void owners_failure(void) { _Exit(73); }
void *owners_allocate(size_t n) { return malloc(n); }
void owners_release(void *p) { free(p); }
''')
    (work / "probe.c").write_text(r'''
#include "Owners.h"
#include <stdio.h>
int main(void) {
    void *const p = owners_allocate(37);
    if (!p) return 2;
    size_t n = __builtin_object_size(p, 0);
    owners_release(p);
    printf("%zu\n", n);
    if (CESTA_CHECKED_OPTIMIZE) return n != 37;
    return n != (size_t)-1;
}
''')
    rows = []
    for compiler in ("gcc", "clang"):
        for hints in (0, 1):
            flags = ["-std=c11", "-g0", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                     f"-DCESTA_CHECKED_OPTIMIZE={hints}", "-I", work]
            for optimization in ("-O2", "-O3"):
                stem = f"{compiler}-{hints}-{optimization[1:]}"
                plain, authored = work / stem, work / (stem + "-authored")
                run([compiler, *flags, optimization, *exported, work / "runtime.c", "-o", plain])
                run([BINARY, "cc", "--cc", compiler, *flags, optimization, *sources,
                     work / "runtime.c", "-o", authored])
                assert plain.read_bytes() == authored.read_bytes(), stem
                for executable in (plain, authored):
                    run([executable], cwd=work)
                    for fault in ("bounds", "overflow", "shift", "division", "require"):
                        result = run([executable, fault], cwd=work, success=False)
                        assert result.returncode == 73, (stem, fault, result.returncode)
                rows.append({"compiler": compiler, "hints": hints,
                             "optimization": optimization, "sha256": digest(plain),
                             "bytes": plain.stat().st_size})
            probe = work / f"{compiler}-{hints}-extent"
            run([compiler, *flags, "-O2", work / "probe.c", work / "extent.c", "-o", probe])
            result=run([probe], cwd=work)
            assert result.stdout.strip()==str(37 if hints else (1<<64)-1).encode()
            invalid = run([compiler, *flags, "-UCESTA_CHECKED_OPTIMIZE",
                           "-DCESTA_CHECKED_OPTIMIZE=2", "-fsyntax-only", exported[0]], success=False)
            assert b"must be 0 or 1" in invalid.stderr
            sanitized = work / f"{compiler}-{hints}-sanitized"
            run([compiler, *flags, "-O1", "-fsanitize=address,undefined",
                 "-fno-sanitize-recover=all", *exported, work / "runtime.c", "-o", sanitized])
            run([sanitized], cwd=work)
            for fault in ("bounds", "overflow", "shift", "division", "require"):
                result = run([sanitized, fault], cwd=work, success=False)
                assert result.returncode == 73, (compiler, hints, fault, result.stderr)
    for name,source in zip(names,sources):
        target=work/(name+'.cesta')
        if source.resolve()!=target.resolve():shutil.copyfile(source,target)
    (work/'cesta.project').write_text('''(cesta project/1 (out "project-out")
      (profile checked (flags "-std=c11" "-O2" "-g0" "-Wall" "-Wextra" "-Wpedantic" "-Werror"))
      (target suite (profile checked) (include ".")
        (sources "ByteViews.cesta" "Owners.cesta" "Extra.cesta" "runtime.c"))
      (target probe (profile checked) (include ".") (sources "probe.c" "extent.c")))
''')
    project_rows=[]
    for compiler in ('gcc','clang'):
        for hints in (0,1,0):
            command=[BINARY,'build','-C',work,'--cc',compiler]
            if hints:command+=['--checked-hints']
            run(command,cwd=work)
            probe=work/'project-out/probe/probe'
            expected=str(37 if hints else (1<<64)-1).encode()
            assert run([probe],cwd=work).stdout.strip()==expected,'stale project hint mode'
            suite=work/'project-out/suite/suite'
            run([suite],cwd=work)
            fault=run([suite,'bounds'],cwd=work,success=False)
            assert fault.returncode==73
            destination=work/f'export-{compiler}-{hints}'
            run([*command,'--export-c',destination],cwd=work)
            run(['make','-C',destination,'CC='+compiler,'-j1'],cwd=work)
            for target in ('probe','suite'):
                authored=work/f'project-out/{target}/{target}'
                exported=destination/f'project-out/{target}/{target}'
                assert authored.read_bytes()==exported.read_bytes(),(compiler,hints,target,'project export')
            assert run([destination/'project-out/probe/probe'],cwd=work).stdout.strip()==expected
            project_rows.append(dict(compiler=compiler,hints=hints,probe_extent=expected.decode()))
    result = {"compiler_sha256": digest(BINARY), "builds": rows,"project_builds":project_rows,
              "extent_probes": 4, "sanitizer_builds": 4, "invalid_modes_refused": 4}
    (work / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    (ROOT / "build/checked-hints-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print("checked hints: 8 authored/export identities, 120 runtime controls, "
          "4 allocator-extent probes, 4 invalid modes refused, 6 project mode transitions; GCC/Clang and ASan/UBSan")


if __name__ == "__main__":
    main()
