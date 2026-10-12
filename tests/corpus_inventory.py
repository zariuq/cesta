"""Account for SWI C files in their configured translation-unit contexts."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shlex

from corpus import ROOT, run, swi_units


def references(path, spelling):
    return [{"file":str(path),"line":i,"text":line.strip()}
            for i,line in enumerate(path.read_text().splitlines(),1) if spelling in line]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commands",type=Path,default=ROOT / "build/swipl-build-commands.txt")
    parser.add_argument("--output",type=Path,default=ROOT / "build/corpus/swi-inventory")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("source inventory artifacts belong under build")
    output.mkdir(parents=True,exist_ok=True)
    checkout = ROOT.parent / "repos/swipl-devel"
    src = checkout / "src"
    cwd,commands,_ = swi_units(args.commands)
    by_source = defaultdict(list)
    included = defaultdict(set)
    for i,command in enumerate(commands):
        source = Path(command["source"])
        by_source[source].append(i)
        result,data = run(command["compiler"]+command["flags"]+["-g0","-M","-MT","cesta-dependencies",str(source)],cwd)
        if not result["accepted"]:
            raise RuntimeError(f"cannot determine active source contexts: {source}: {result['diagnostic']}")
        words = shlex.split(data.decode().replace("\\\n"," ").split(":",1)[1])
        for word in words:
            dep = (cwd / word).resolve()
            if dep != source and dep.suffix == ".c" and dep.is_relative_to(src):
                included[dep].add(source)
    cmake = src / "CMakeLists.txt"
    windows = checkout / "cmake/port/Windows.cmake"
    wasm = checkout / "cmake/port/Emscripten.cmake"
    rows = []
    for path in sorted(src.rglob("*.c")):
        name = str(path.relative_to(src))
        evidence = []
        if path in by_source:
            context = "configured translation unit"
            evidence = [{"command_index":i} for i in by_source[path]]
        elif path in included:
            context = "included C fragment"
            evidence = [{"translation_unit":str(parent.relative_to(src))} for parent in sorted(included[path])]
        elif name.startswith("libbf/") or name == "pl-bf.c":
            context = "alternative libbf arithmetic; USE_LIBBF=OFF"
            evidence = references(cmake,"if(USE_LIBBF)")+references(cmake,path.name)
        elif name.startswith("libtai/"):
            context = "bundled libtai routines/utilities outside the selected SRC_TAI list"
            evidence = references(cmake,"set(SRC_TAI")
        elif name.startswith("os/windows/") or name in ("pl-nt.c","pl-ntconsole.c","pl-dde.c"):
            context = "Windows alternative outside the configured Linux build"
            evidence = references(windows,path.name)+references(cmake,path.name)
        elif name == "minizip/iowin32.c":
            context = "bundled Windows minizip I/O alternative"
            evidence = references(src / "minizip/ioapi.h","iowin32.c")
        elif name in ("os/pl-apple.c","os/pl-beos.c"):
            context = "Apple/BeOS alternative outside the configured Linux build"
            evidence = references(cmake,path.name)
        elif name == "wasm/pl-wasm.c":
            context = "Emscripten alternative outside the configured Linux build"
            evidence = references(wasm,path.name)
        elif name == "wrap-gcc.c":
            context = "excluded optional GCC PGO helper"
            evidence = references(cmake,"add_executable(wrap-gcc")
        elif name.startswith("test/"):
            context = "standalone C test utility outside the configured build commands"
            evidence = references(path,"main(")+references(path,"main (")
        elif name in ("minizip/miniunz.c","minizip/minizip.c","minizip/mztools.c"):
            context = "bundled minizip standalone utilities/helpers outside SRC_MINIZIP"
            evidence = references(cmake,"set(SRC_MINIZIP")
        elif name == "compat/yap.c":
            context = "optional YAP foreign-interface compatibility source"
            evidence = references(path,"gcc -I.. -c yap.c")
        else:
            raise RuntimeError(f"unclassified C source context: {name}")
        rows.append({"source":name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                     "context":context,"evidence":evidence})
    settings = {}
    for line in (checkout / "build/CMakeCache.txt").read_text().splitlines():
        match = re.fullmatch(r'(CMAKE_BUILD_TYPE|CMAKE_C_COMPILER|CMAKE_INSTALL_PREFIX|USE_GMP|USE_LIBBF):[^=]+=(.*)',line)
        if match: settings[match[1]] = match[2]
    fragments = {path:parents for path,parents in included.items() if path not in by_source}
    included_units = {str(path.relative_to(src)):[str(parent.relative_to(src)) for parent in sorted(parents)]
                      for path,parents in included.items() if path in by_source}
    summary = {"files":len(rows),"compile_commands":len(commands),"unique_translation_units":len(by_source),
               "included_fragments":len(fragments),"translation_units_also_included":included_units,
               "contexts":dict(Counter(row["context"] for row in rows)),
               "configuration":settings,"sources":rows,
               "commands_sha256":hashlib.sha256(args.commands.read_bytes()).hexdigest(),
               "claim":"Configured translation units are qualified by the corpus gate; included C files are qualified in those actual contexts. Other files are accounted for, not independently qualified."}
    (output / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    if len(rows) != len(by_source)+len(fragments)+sum(row['context'] not in ('configured translation unit','included C fragment') for row in rows):
        raise RuntimeError("source context categories overlap")
    print(json.dumps({key:summary[key] for key in ("files","compile_commands","unique_translation_units","included_fragments","contexts")}))


if __name__ == "__main__":
    main()
