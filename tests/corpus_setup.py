"""Read existing build configurations without building or editing the corpora."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def capture(arguments, cwd):
    result = subprocess.run([str(x) for x in arguments], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cetta", type=Path, default=ROOT.parent / "hyperon/CeTTa")
    parser.add_argument("--swi", type=Path, default=ROOT.parent / "repos/swipl-devel")
    args = parser.parse_args()
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    cetta, swi = args.cetta.resolve(), args.swi.resolve()
    original = (cetta / "Makefile").read_text()
    dependency_line = "-include $(BUILD_CONFIG_STAMP) $(STAGE0_BUILD_CONFIG_STAMP) $(DEPS)"
    if original.count(dependency_line) != 1:
        raise RuntimeError("CeTTa dependency-import layout changed; audit its read-only flag extraction")
    source = original.replace(dependency_line, "")
    flags = output / "cetta-build-flags.txt"
    source += "\n.PHONY: cesta-corpus-inputs\ncesta-corpus-inputs:\n"
    for index,name in enumerate(("CC","CPPFLAGS","CFLAGS","LDFLAGS","SRC","BUILD_OBJ_TAG","BUILD_CONFIG_HEADER")):
        source += f"\t$(file {'>' if index == 0 else '>>'}{flags},{name}=$({name}))\n"
    source += "\t@:\n"
    copied = output / "cetta-read-only-flags.mk"
    copied.write_text(source)
    # Only the local copy's flag-output expressions run. Make's dry-run mode
    # refuses recipes, and imported dependency makefiles were removed above.
    capture(["make","--no-print-directory","-n","-s","BUILD=core","-f",copied,"cesta-corpus-inputs"],cetta)
    for tool,name in (("commands","swipl-build-commands.txt"),("deps","swipl-build-dependencies.txt")):
        (output / name).write_bytes(capture(["ninja","-C",swi / "build","-t",tool],ROOT))
    evidence = {"cetta_makefile_sha256":hashlib.sha256(original.encode()).hexdigest(),
                "cetta_flags_sha256":hashlib.sha256(flags.read_bytes()).hexdigest(),
                "swi_commands_sha256":hashlib.sha256((output / "swipl-build-commands.txt").read_bytes()).hexdigest(),
                "swi_dependencies_sha256":hashlib.sha256((output / "swipl-build-dependencies.txt").read_bytes()).hexdigest()}
    (output / "corpus-configuration.json").write_text(json.dumps(evidence,indent=2)+"\n")
    print("corpus configuration: CeTTa BUILD=core flags and existing SWI compiler commands/dependencies recorded read-only")


if __name__ == "__main__":
    main()
