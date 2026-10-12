"""Qualify the built-in source commands and standalone production build."""

import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, expected=0, cwd=ROOT):
    result = subprocess.run([str(word) for word in arguments], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expected:
        raise SystemExit(f"{arguments}: status {result.returncode}: {result.stderr.decode()}")
    return result


def main():
    output = ROOT / "build/workbench"
    output.mkdir(parents=True, exist_ok=True)
    source = output / "source.c"
    source.write_text("int asm,typeof;int main(void){asm=3;typeof=4;return asm+typeof==7?0:1;}\n")
    wire = output / "source.csexp"
    wire.write_bytes(run([BINARY,"read","--std=c11",source]).stdout)
    printed = output / "printed.c"
    printed.write_bytes(run([BINARY,"write","--std=c11",wire]).stdout)
    if run([BINARY,"read","--std=c11",printed]).stdout != wire.read_bytes():
        raise SystemExit("built-in source commands change the AST")
    run([BINARY,"read","--std=gnu11",source], expected=1)
    for path in ("grammar/CExternal.module","grammar/GNU.module"):
        run([BINARY,"check-module",path])
    own = run([BINARY,"module-grammar","grammar/CExternal.module","C11"]).stdout
    generic = run([BINARY,"module-grammar","grammar/TheoryModule.cf",
                   "grammar/CExternal.module","C11"]).stdout
    if own != generic:
        raise SystemExit("built-in module adapter changes activated theory data")
    run([BINARY,"check-module","tests/modules/DuplicateParameter.module"],expected=1)
    run([BINARY,"check-module","tests/modules/UnboundActivation.module"],expected=1)
    # A fresh source copy proves that make has no upstream syntax/runtime
    # dependency; no repository metadata or external corpus is copied.
    portable = output / "standalone"
    portable.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "Makefile",portable / "Makefile")
    for directory in ("src","tools","grammar","presentations"):
        target = portable / directory
        target.mkdir(exist_ok=True)
        for path in sorted((ROOT / directory).iterdir()):
            if path.is_file():
                shutil.copy2(path,target / path.name)
    run(["make","-j2","all","REFERENCE_SYNTAX=unavailable-upstream.cf"],cwd=portable)
    standalone = portable / "build/cesta"
    for compiler in ("gcc","clang"):
        executable = output / (compiler + "-standalone")
        run([standalone,"cc","--cc",compiler,"-std=c11","-Wall","-Wextra","-Werror",
             wire,"-o",executable])
        run([executable])
    (output / "summary.json").write_text(json.dumps({"builtin_codec":True,
        "builtin_module_adapter":True,"standalone_make":True,"compilers":["gcc","clang"]},indent=2)+"\n")
    print("workbench: built-in codecs/modules and a fresh standalone make build passed")


if __name__ == "__main__":
    main()
