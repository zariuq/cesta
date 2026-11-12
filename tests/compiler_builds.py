"""Build the production tool with both compilers at both optimization levels.

The Makefile supplies the actual production compile command. Each independently
built binary must preprocess and elaborate the same macro-containing source.
"""

import argparse
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(arguments):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(" ".join(map(str, arguments)) + "\n" +
                           result.stderr.decode(errors="replace")[-6000:])
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sanitizers-only', action='store_true',
                        help='build and exercise a Clang ASan/UBSan compiler')
    args = parser.parse_args()
    work = ROOT / "build/compiler-builds"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "portable.c"
    source.write_text("/* source and expansion are both exercised */\n"
                      "#define TWICE(x) ((x) + (x))\n"
                      "_Static_assert(TWICE(3) == 6, \"expansion\");\n"
                      "unsigned f(unsigned x) { return TWICE(x); }\n")
    foreign = work / "foreign.cesta"
    foreign.write_text('''(cesta checked-c/1 (module boundary) (failure boundary_failure)
      (foreign mix native_mix (params (a (span u32 read) data) (b (span u32 write) data_len))
        (returns unit) (effects) (obligation "Reads a and writes b; retains no pointer."))
      (fn call_mix (params (a (span u32 read)) (b (span u32 write)))
        (returns unit) (effects) (body (call mix a b) (return))))''')
    control = work / "control.cesta"
    control.write_text('''(cesta native-c/1 compact-c/2 (unit (ownership (check-all))
      (native "int f(int n){int value;do{value=n+1;if(n)goto done;}while(0);done:return value;}")))''')
    callback = work / "callback.cesta"
    callback.write_text('''(cesta native-c/1 compact-c/2 (unit
      (ownership (check-all)
        (callback ReadNext (field Reader read) (returns-borrow 0))
        (record Reader (field context (borrow read)) (field read (callback ReadNext)))
        (function current (returns-borrow 0)))
      (native "typedef struct Reader {const unsigned *context; const unsigned *(*read)(const unsigned *);} Reader;
      const unsigned *current(const unsigned *p) {return p;}
      unsigned apply(Reader r) {return *r.read(r.context);}
      unsigned f(void) {const unsigned n=7;Reader r={&n,current};return apply(r);}")))''')
    results, contracts, control_plans, control_exports, callback_plans = {}, {}, {}, {}, {}
    for compiler in (("clang",) if args.sanitizers_only else ("gcc", "clang")):
        for level in (("1",) if args.sanitizers_only else ("0", "2")):
            instrument = ' -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer' if args.sanitizers_only else ''
            commands = run(["make", "-s", "-n", "-B", "build/cesta",
                            "CC=" + compiler, "CFLAGS=-O" + level + " -g0" + instrument])
            production = [shlex.split(line) for line in commands.decode().splitlines()
                          if "-DCE_BUILTIN_LANGUAGES" in line]
            if len(production) != 1:
                raise RuntimeError("expected one production compile command")
            arguments = production[0]
            binary = work / (compiler + "-O" + level + ('-sanitize' if args.sanitizers_only else ''))
            arguments[arguments.index("-o") + 1] = str(binary)
            run(arguments)
            result = run([str(binary), "types", "--cc", compiler,
                          "-std=gnu11", str(source)])
            if b"(types" not in result or b"(from \"TWICE\"" not in result:
                raise RuntimeError("portable build did not retain typed macro provenance")
            results[compiler, level] = result
            contracts[compiler, level] = (
                run([str(binary), "checked", "contract", "checked/examples/CopySpan.cesta"]),
                run([str(binary), "checked", "contract", str(foreign)]))
            foreign_c = run([str(binary), "export-c", str(foreign)])
            if b"void native_mix(const uint32_t *, uint32_t *, size_t);" not in foreign_c:
                raise RuntimeError("host build changed the foreign span ABI projection")
            plan = work / (compiler+"-O"+level+"-control.plan")
            control_exports[compiler,level] = run([str(binary),"export-c",str(control),
                "--cc",compiler,"--std=c11","--plan",str(plan)])
            control_plans[compiler,level] = plan.read_bytes()
            if b'cesta-ownership/6' not in control_plans[compiler,level] or \
                    b'(once (first' not in control_plans[compiler,level]:
                raise RuntimeError("host build lost post-test ownership control")
            plan = work / (compiler+"-O"+level+"-callback.plan")
            run([str(binary), "export-c", str(callback), "--cc", compiler, "--std=c11", "--plan", str(plan)])
            callback_plans[compiler,level] = plan.read_bytes()
            if b'cesta-ownership/7' not in callback_plans[compiler,level] or \
                    b'(ReadNext current)' not in callback_plans[compiler,level]:
                raise RuntimeError("host build lost field-derived callback implementation evidence")
            # A native source also contributes empty output slots. Those must
            # not become memcpy(NULL, ..., 0) calls while publishing records.
            record = work / (compiler+"-O"+level+"-callback.contracts")
            run([str(binary), "cc", "--cc", compiler, "-std=c11", "--contracts", str(record),
                 "-fsyntax-only", str(callback)])
            if b'link-contracts/1' not in record.read_bytes() or b'(open)' not in record.read_bytes():
                raise RuntimeError("compiler did not publish its actual callback link record")
            print(compiler, "-O" + level, "production build and typed provenance pass", flush=True)
    for compiler in ("gcc", "clang"):
        if (compiler, "0") in results and results[compiler, "0"] != results[compiler, "2"]:
            raise RuntimeError(compiler + ": optimization changed the tool's analysis")
        if (compiler, "0") in control_plans and control_plans[compiler,"0"] != control_plans[compiler,"2"]:
            raise RuntimeError(compiler+": optimization changed ownership control admission")
    if len(set(control_exports.values())) != 1:
        raise RuntimeError("host compiler/optimization changed the exported control flow")
    if len(set(contracts.values())) != 1:
        raise RuntimeError("host compiler/optimization changed the emitted span contract")
    if len(set(callback_plans.values())) != 1:
        raise RuntimeError("host compiler/optimization changed field-derived callback admission")
    if args.sanitizers_only:
        print("instrumented compiler: span, post-test control, callback plans and link publication pass", flush=True)
    else:
        print("span contract identical across the four production-tool builds", flush=True)
        print("field-derived callback plan identical across the four production-tool builds", flush=True)


if __name__ == "__main__":
    main()
