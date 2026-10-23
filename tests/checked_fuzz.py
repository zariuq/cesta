"""Generated ownership programs: independent kernel plans and native safety.

Candidate programs combine scopes, moves, calls, allocation, branches and
loops. The generator does not implement a borrow checker: it deliberately
chooses dead and incompatible names as well. Every accepted program must pass
the independent permission checker and native allocation-accounting controls.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import tempfile

import checked_admission as admission
from checked_core import BINARY, ROOT, run
from oracles.pro_compact_cesta import parse


def candidate(number, generator):
    names = ["first", "second"]
    statements = ["(array first 4)", "(array second 4)"]
    counter = 0

    def fresh():
        nonlocal counter
        counter += 1
        return "value_" + str(counter)

    def read_block(root):
        view, copied, scalar = fresh(), fresh(), fresh()
        return (f"(borrow {view} read {root}) (copy {copied} {view}) (end {view}) "
                f"(let {scalar} u8 (at {copied} (usize {generator.randrange(4)}))) (end {copied})")

    for _ in range(generator.randrange(5, 15)):
        root = generator.choice(names)
        action = generator.randrange(10)
        if action == 0:
            statements.append(read_block(root))
        elif action == 1:
            view, child, copied = fresh(), fresh(), fresh()
            statements.append(f"(block (borrow {view} write {root}) (borrow {child} read {view}) "
                f"(copy {copied} {child}) (end {child}) (call inspect {copied}) (end {copied}) "
                f"(put {view} (usize 0) (u8 {generator.randrange(256)})) (end {view}))")
        elif action == 2:
            moved = fresh()
            statements.append(f"(move {moved} {root})")
            names.append(moved)
        elif action == 3:
            statements.append(f"(put {root} (usize {generator.randrange(4)}) (u8 {generator.randrange(256)}))")
        elif action == 4:
            statements.append(f"(if {'true' if generator.randrange(2) else 'false'} "
                f"(then {read_block(root)}) (else (call paint {root})))")
        elif action == 5:
            index, view = fresh(), fresh()
            statements.append(f"(for {index} {root} (body (borrow {view} read {root}) (call inspect {view}) (end {view})))")
        elif action == 6:
            allocation, moved, view = fresh(), fresh(), fresh()
            statements.append(f"(allocate {allocation} (usize 4) "
                f"(then (borrow {view} read {allocation}) (call inspect {view}) (end {view}) "
                f"(let {moved} own (call identity {allocation})) (call consume {moved})) (else))")
        elif action == 7:
            local, view = fresh(), fresh()
            statements.append(f"(block (array {local} 4) (borrow {view} write {local}) "
                f"(call paint {view}) (end {view}))")
        elif action == 8:
            statements.append(f"(drop {root})")
        else:
            view = fresh()
            statements.append(f"(borrow {view} {'read' if generator.randrange(2) else 'write'} {root})")
            names.append(view)
    statements.append("(return)")
    return f"""(cesta checked-c/1 (module fuzz_{number}) (failure fuzz_failure)
      (allocator fuzz_allocate fuzz_release)
      (fn inspect (params (bytes read)) (returns u8) (effects)
        (body (return (at bytes (usize 0)))))
      (fn paint (params (bytes write)) (returns unit) (effects)
        (body (put bytes (usize 0) (u8 17)) (return)))
      (fn consume (params (bytes own)) (returns unit) (effects heap) (body (return)))
      (fn identity (params (bytes own)) (returns own) (effects) (body (return bytes)))
      (fn run (params) (returns unit) (effects heap) (body {' '.join(statements)})))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=512)
    parser.add_argument("--lean-project", type=Path, default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb", type=int)
    args = parser.parse_args()
    generator = random.Random(0xB0770)
    work = Path(tempfile.mkdtemp(prefix="checked-generated-", dir=ROOT / "build"))
    initial_hash = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    model_hashes = {}
    for name in admission.MODULES:
        source = args.lean_project / (name.replace(".", "/") + ".lean")
        artifact = args.lean_project / ".lake/build/lib/lean" / (name.replace(".", "/") + ".olean")
        if not artifact.exists() or artifact.stat().st_mtime_ns < source.stat().st_mtime_ns:
            raise RuntimeError("build the current permission model before generated admission")
        model_hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    declarations, calls, exported, decisions = [], [], [], []
    rejected, accepted, reasons = 0, 0, {}
    for number in range(args.cases):
        source = work / (str(number) + ".cesta")
        source.write_text(candidate(number, generator))
        result = subprocess.run([BINARY, "checked", "check", source], capture_output=True)
        if result.returncode:
            assert b"error:" in result.stderr and result.returncode == 1, result.stderr
            message = result.stderr.decode().split("error:", 1)[1].splitlines()[0].strip()
            reasons[message] = reasons.get(message, 0) + 1
            rejected += 1
            continue
        plan = run([BINARY, "checked", "plan", source]).stdout
        (work / (str(number) + ".plan")).write_bytes(plan)
        records = parse(plan)
        assert len(records) == 1
        decisions += [f"def program{number} : Module := " + admission.module(records[0]),
                      f"theorem admitted{number} : admit program{number} = true := by decide",
                      f"#print axioms admitted{number}"]
        target = work / (str(number) + ".c")
        run([BINARY, "export-c", source, "-o", target, "--"])
        exported.append(target)
        declarations.append(f"void fuzz_{number}_run(void);")
        calls.append(f"fuzz_{number}_run(); assert(live == 0);")
        accepted += 1
    assert accepted >= 10 and rejected >= 10, (accepted, rejected)
    generated = work / "Generated.lean"
    generated.write_text("\n\n".join([*("import " + name for name in admission.MODULES),
        "set_option autoImplicit false", "set_option Elab.async false", "set_option maxRecDepth 32768",
        "set_option maxHeartbeats 50000000", "open " + admission.MODEL, *decisions]) + "\n")
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if args.lean_memory_mb:
        command += ["-M", str(args.lean_memory_mb)]
    result = subprocess.run([*command, generated], cwd=args.lean_project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (work / "lean.log").write_bytes(result.stdout)
    if result.returncode:
        raise AssertionError((generated, result.stdout.decode(errors="replace")[-12000:]))
    for text in re.findall(r"depends on axioms: \[(.*?)\]", result.stdout.decode(), re.S):
        assert {name.strip() for name in text.split(",") if name.strip()} <= admission.AXIOMS
    harness = work / "runtime.c"
    harness.write_text("#include <assert.h>\n#include <stdlib.h>\n#include <stdbool.h>\n#include <stddef.h>\n"
        "static size_t live; static bool refuse;\n"
        "void fuzz_failure(void) { _Exit(71); }\n"
        "void *fuzz_allocate(size_t bytes) { if (refuse) return NULL; void *p=malloc(bytes); if(p) ++live; return p; }\n"
        "void fuzz_release(void *p) { assert(live); --live; free(p); }\n" + "\n".join(declarations) +
        "\nint main(void) { for(int phase=0;phase<2;++phase) { refuse=phase!=0; " + "\n".join(calls) + " } return 0; }\n")
    for compiler, sanitized in (("gcc", False), ("clang", True)):
        executable = work / compiler
        flags = ["-std=c11", "-O1" if sanitized else "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
        if sanitized:
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        run([compiler, *flags, *exported, harness, "-o", executable])
        run([executable], cwd=work)
    assert initial_hash == hashlib.sha256(BINARY.read_bytes()).hexdigest(), "generator changed during qualification"
    for name, before in model_hashes.items():
        source = args.lean_project / (name.replace(".", "/") + ".lean")
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before, "permission model changed during qualification"
    summary = {"binary_sha256": initial_hash, "models": model_hashes, "candidates": args.cases,
               "accepted_and_kernel_admitted": accepted, "refused": rejected,
               "refusal_reasons": reasons, "native_controls": "GCC and Clang ASan/UBSan, allocation success and failure",
               "work": str(work)}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "build/checked-generated-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"generated permissions: {accepted} native/kernel-admitted programs; {rejected} source refusals; no allocation leaks")


if __name__ == "__main__":
    main()
