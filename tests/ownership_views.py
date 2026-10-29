"""Join native reference records to the existing checked-byte implementation."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import checked_admission
import ownership_admission

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "ownership/examples/ByteViews"


def run(command, expected=0, **kwargs):
    result = subprocess.run(list(map(str, command)), capture_output=True, **kwargs)
    assert result.returncode == expected, (command, result.returncode, result.stdout[-3000:], result.stderr[-5000:])
    return result


def erase_ownership(source):
    start = source.index("(ownership")
    depth, quoted, escaped = 0, False, False
    for end in range(start, len(source)):
        c = source[end]
        if quoted:
            if escaped: escaped = False
            elif c == "\\": escaped = True
            elif c == '"': quoted = False
        elif c == '"': quoted = True
        elif c == '(': depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return source[:start] + source[end+1:]
    raise AssertionError("unterminated ownership declaration")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cesta", type=Path, default=ROOT / "build/cesta")
    p.add_argument("--output", type=Path, default=ROOT / "build/ownership-views")
    p.add_argument("--lean-root", type=Path)
    p.add_argument("--admit", action="store_true")
    args = p.parse_args()
    if args.admit and not args.lean_root:
        args.lean_root = ROOT.parent / "Mettapedia/lean/mettapedia"
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    source = EXAMPLE / "main.cesta"
    byte_source = EXAMPLE / "bytes.cesta"
    header = work / "view_bytes.h"
    header.write_bytes(run([args.cesta, "checked", "header", byte_source]).stdout)
    byte_c = work / "bytes.c"
    run([args.cesta, "export-c", byte_source, "-o", byte_c])
    provider = work / "failure.c"
    provider.write_text('#include <stdlib.h>\nvoid view_bytes_failure(void) { exit(77); }\n')
    (work / "erased").mkdir(exist_ok=True)
    erased = work / "erased/main.cesta"
    erased.write_text(erase_ownership(source.read_text()))
    runs, hashes = 0, []
    for compiler in ("gcc", "clang"):
        (work / compiler).mkdir(exist_ok=True)
        exported = work / compiler / "main.c"
        plan = work / (compiler + ".plan")
        run([args.cesta, "export-c", source, "--cc", compiler, "--std=c11", "-o", exported, "--plan", plan, "--", "-I"+str(work)])
        for sanitized in (False, True):
            flags = ["-std=c11", "-O2", "-g0", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-I"+str(work)]
            if sanitized:
                flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
            binaries = []
            for route, main_source in (("authored", source), ("exported", exported), ("erased", erased)):
                binary = work / f"{compiler}-{int(sanitized)}-{route}"
                command = [args.cesta, "cc", "--cc", compiler] if route != "exported" else [compiler]
                run([*command, *flags, main_source, byte_c, provider, "-o", binary])
                run([binary]); runs += 1; binaries.append(binary)
            if not sanitized:
                digests = [hashlib.sha256(b.read_bytes()).hexdigest() for b in binaries]
                assert len(set(digests)) == 1, (compiler, digests)
                hashes.append({"compiler": compiler, "sha256": digests[0]})
        # Native admission accepts the call's resource use; the checked byte
        # body still executes its explicit bounds guard for an invalid index.
        bounds = work / f"bounds-{compiler}.cesta"
        old = "(call view_bytes_sum first)"
        text = source.read_text()
        assert text.count(old) == 1
        bounds.write_text(text.replace(old, "(cast uint64_t (call view_bytes_at_index first 8))"))
        binary = work / f"bounds-{compiler}"
        run([args.cesta, "cc", "--cc", compiler, *flags, bounds, byte_c, provider, "-o", binary])
        run([binary], expected=77); runs += 1
    admission = None
    if args.lean_root:
        forms = ownership_admission.parse((work / "gcc.plan").read_bytes())
        admission = ownership_admission.admit_record(forms[0], work / "NativeViews.lean", args.lean_root.resolve())
        byte_plan = run([args.cesta, "checked", "plan", byte_source]).stdout
        (work / "bytes.plan").write_bytes(byte_plan)
        record, = checked_admission.parse(byte_plan)
        model = checked_admission.module(record)
        declarations = ["import Mettapedia.GSLT.LanguageDef.CheckedByteModules",
            "set_option maxRecDepth 200000", "set_option maxHeartbeats 0",
            "open Mettapedia.GSLT.LanguageDef.CheckedBytes",
            "def actual : Module := " + model,
            "example : admit actual = true := by decide +kernel"]
        mutants = checked_admission.mutations(record)
        for _, mutant in mutants:
            declarations.append("example : admit ("+checked_admission.module(mutant)+" : Module) = false := by decide +kernel")
        proof = work / "ByteViews.lean"
        proof.write_text("\n".join(declarations)+"\n")
        run(["lake", "env", "lean", proof], cwd=args.lean_root.resolve())
        admission["checked_byte_mutants"] = len(mutants)
    (work / "result.json").write_text(json.dumps({"executions": runs, "erasure_identity": hashes,
        "admission": admission}, indent=2)+"\n")
    print(f"ownership byte views: {runs} authored/exported/erased executions and guarded failures; GCC/Clang, sanitizers, binary identity" + ("; both permission plans kernel-admitted" if admission else ""))


if __name__ == "__main__":
    main()
