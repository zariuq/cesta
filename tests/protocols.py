"""Executable protocols against independent source traces and real C compilers."""

from pathlib import Path
import copy
import hashlib
import json
import re
import subprocess
import tempfile

from oracles.pro_compact_cesta import Sym, dumps, parse

ROOT = Path(__file__).resolve().parent.parent
CESTA = ROOT / "build/cesta"
OUTCOMES = ["declined", "answered", "done", "raised", "suspended",
            "resource_failure", "engine_failure"]


def run(args, *, ok=True, **kwargs):
    kwargs.setdefault("cwd", ROOT)
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, **kwargs)
    if ok and result.returncode:
        raise AssertionError((args, result.returncode, result.stdout, result.stderr))
    if not ok and not result.returncode:
        raise AssertionError(("accepted invalid input", args, result.stdout))
    return result


def declaration(path):
    source = path.read_text()
    found = re.search(r"\((\w+)\)\s*==\s*(\(Protocol.*)\);", source, re.S)
    assert found, path
    return found[1], parse((found[2] + ")").encode())[0]


def module(name, data):
    return ("Module TestProtocol { Theory Protocols() { Empty Equations {\n"
            f"({name}) == {dumps(data)};\n" + "} } }\n")


def semantics(data, bits):
    """Interpret the authored source, without consulting the lowered target."""
    hooks = {str(h[1]): h for h in data[3][1:]}
    resources = {str(r[1]): r for r in data[4][1:]}
    used, world, trace = 0, 17, []

    def event(name, boolean):
        nonlocal used, world
        reply = (bits >> used) & 1 if boolean else 2
        used += int(boolean)
        # Independent host state: every call, including cleanup, changes it.
        for byte in str(name).encode():
            world = (world * 33 + byte) & ((1 << 64) - 1)
        world = (world * 33 + reply) & ((1 << 64) - 1)
        trace.append(f"{name}:{reply}")
        return reply

    body = data[6][1]
    while body[0] != "Return":
        kind, ident = map(str, body[:2])
        if kind == "Call":
            reply = event(hooks[ident][2], True)
            body = body[2 if reply else 3]
        elif kind == "Acquire":
            reply = event(resources[ident][2], True)
            body = body[2 if reply else 3]
        else:
            assert kind in ("Release", "Transfer")
            event(resources[ident][3 if kind == "Release" else 4], False)
            body = body[2]
    return f"{OUTCOMES.index(str(body[1]))} {used} {world} " + " ".join(trace)


def bool_depth(body):
    if body[0] == "Return":
        return 0
    if body[0] in ("Call", "Acquire"):
        return 1 + max(bool_depth(body[2]), bool_depth(body[3]))
    return bool_depth(body[2])


def harness(name, data, header):
    context = str(data[2][1])
    lines = ["#include <stdbool.h>", "#include <stdint.h>", "#include <stdio.h>",
             "#include <stdlib.h>",
             f"typedef struct {context} {{ unsigned bits, used; uint64_t world; char trace[4096]; size_t len; }} {context};",
             f"static inline bool event({context} *s, const char *name, bool boolean) {{",
             "  unsigned reply = boolean ? (s->bits >> s->used++) & 1u : 2u;",
             "  for (const unsigned char *p = (const unsigned char *)name; *p; ++p) s->world = s->world * 33 + *p;",
             "  s->world = s->world * 33 + reply;",
             '  int count = snprintf(s->trace+s->len, sizeof(s->trace)-s->len, "%s%s:%u", s->len ? " " : "", name, reply);',
             "  if (count < 0 || (size_t)count >= sizeof(s->trace)-s->len) abort();",
             "  s->len += (size_t)count; return reply != 0;", "}"]
    names = [(str(h[2]), True) for h in data[3][1:]]
    for r in data[4][1:]:
        names += [(str(r[2]), True), (str(r[3]), False), (str(r[4]), False)]
    used = set()
    def collect(body):
        kind = str(body[0])
        if kind == "Return":
            return
        if kind == "Call":
            used.add(str(next(h[2] for h in data[3][1:] if h[1] == body[1])))
        else:
            resource = next(r for r in data[4][1:] if r[1] == body[1])
            used.add(str(resource[{"Acquire": 2, "Release": 3, "Transfer": 4}[kind]]))
        for child in body[2:]:
            collect(child)
    collect(data[6][1])
    for native, boolean in names:
        if native not in used:
            continue
        lines.append(f"static inline {'bool' if boolean else 'void'} {native}({context} *s) {{ " +
                     ("return " if boolean else "(void)") +
                     f'event(s, "{native}", {str(boolean).lower()}); }}')
    lines += [f'#include "{header.name}"',
              "int main(int argc, char **argv) {",
              "  if (argc != 2) return 2;",
              f"  {context} s = {{.bits=(unsigned)strtoul(argv[1], NULL, 10), .world=17}};",
              f"  unsigned result = {name}(&s);",
              '  printf("%u %u %llu %s\\n", result, s.used, (unsigned long long)s.world, s.trace);',
              "  return 0;", "}"]
    return "\n".join(lines) + "\n"


def mutations(data):
    # Admission fixture locations are deliberate semantic changes.
    def changed(label, change):
        value = copy.deepcopy(data)
        change(value)
        return label, value
    result = [
        changed("missing prerequisite", lambda x: x[3][2][4].append(Sym("not_proved"))),
        changed("undeclared resource", lambda x: x[3][2][5].append(Sym("missing"))),
        changed("duplicate hook", lambda x: x[3].append(copy.deepcopy(x[3][1]))),
        changed("duplicate resource", lambda x: x[4].append(copy.deepcopy(x[4][1]))),
        changed("duplicate fact", lambda x: x[3][1][6].append(Sym("exact_arity"))),
        changed("duplicate acquisition prerequisite", lambda x: x[4][1][5].append(Sym("exact_arity"))),
        changed("missing acquisition prerequisite", lambda x: x[4][1][5].append(Sym("not_proved"))),
        changed("missing transfer prerequisite", lambda x: x[6][1][2].__setitem__(2,
                [Sym("Transfer"), Sym("arguments"), [Sym("Return"), Sym("answered")]])),
        changed("native name collision", lambda x: x[3][2].__setitem__(2, x[3][1][2])),
        changed("parameter shadow", lambda x: x[3][1].__setitem__(2, Sym("ctx"))),
        changed("C keyword", lambda x: x[3][1].__setitem__(2, Sym("while"))),
        changed("unknown kind", lambda x: x[3][1].__setitem__(3, Sym("pure_maybe"))),
        changed("unknown entry owner", lambda x: x[5][2].append(Sym("missing"))),
        changed("unknown opcode", lambda x: x[6][1].__setitem__(0, Sym("Jump"))),
        changed("unknown call", lambda x: x[6][1].__setitem__(1, Sym("missing"))),
        changed("wrong arity", lambda x: x[6][1].append([Sym("Return"), Sym("done")])),
        changed("early preparation", lambda x: x[6].__setitem__(1, x[6][1][2])),
        changed("effect false fallback", lambda x: x[3][1].__setitem__(3, Sym("effect"))),
        changed("leak", lambda x: x[6][1][2].__setitem__(2, [Sym("Return"), Sym("done")])),
        changed("release before acquire", lambda x: x[6].__setitem__(1,
                [Sym("Release"), Sym("arguments"), [Sym("Return"), Sym("done")]])),
        changed("transfer then decline", lambda x: (x[5][1].extend([Sym("exact_arity"), Sym("ready")]), x[6].__setitem__(1,
                [Sym("Acquire"), Sym("arguments"),
                 [Sym("Transfer"), Sym("arguments"), [Sym("Return"), Sym("declined")]],
                 [Sym("Return"), Sym("resource_failure")]]))),
        changed("invalid outcome", lambda x: x[6][1].__setitem__(3,
                [Sym("Return"), Sym("capacity_for_anything")]))]
    return result


def main():
    # Retain failed cases and evidence; never clear another run's directory.
    work = Path(tempfile.mkdtemp(prefix="protocols-", dir=ROOT / "build"))
    source_hash = hashlib.sha256(CESTA.read_bytes()).hexdigest()
    checked, executions, refusals = 0, 0, 0
    for stem in ("Admission", "Delivery"):
        path = ROOT / "protocols" / (stem + ".module")
        name, data = declaration(path)
        output, plan, dep = (work / (stem + suffix) for suffix in (".h", ".sexp", ".d"))
        run([CESTA, "protocol", "export", path, "Protocols", "-o", output,
             "--plan", plan, "--depfile", dep])
        run([CESTA, "protocol", "verify", path, "Protocols", "-o", output, "--plan", plan])
        record = parse(plan.read_bytes())[0]
        assert record[1] == name and record[2:7] == data[2:7], "plan changed authored metadata/body"
        assert str(path) in dep.read_text()
        old = output.read_bytes()
        run([CESTA, "protocol", "export", path, "Protocols", "-o", output])
        assert output.read_bytes() == old
        probe = work / (stem + ".c")
        probe.write_text(harness(name, data, output))
        before_import, after_import = probe.read_text().split(f'#include "{output.name}"')
        document = work / (stem + ".cesta")
        document.write_text('(cesta native-c/1 compact-c/2 (unit\n' +
                            '(native ' + json.dumps(before_import) + ')\n' +
                            '(use-protocols ' + json.dumps(str(path)) + ' Protocols)\n' +
                            '(native ' + json.dumps(after_import) + ')))\n')
        exported = work / (stem + '-exported.c')
        imported_plan = work / (stem + '-imported.plan')
        run([CESTA, 'export-c', document, '-o', exported, '--plan', imported_plan])
        assert imported_plan.read_bytes() == plan.read_bytes()
        for compiler in ("gcc", "clang"):
            for opt in ("-O0", "-O2"):
                binary = work / (stem + compiler + opt)
                run([compiler, "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                     opt, probe, "-o", binary])
                for bits in range(1 << bool_depth(data[6][1])):
                    actual = run([binary, bits]).stdout.rstrip("\n")
                    assert actual == semantics(data, bits), (stem, compiler, opt, bits, actual, semantics(data, bits))
                    executions += 1
            # Authoring, ordinary export and native compiler routing use one target.
            native_binary = work / (stem + '-hosted-' + compiler)
            exported_binary = work / (stem + '-exported-' + compiler)
            dependency = work / (stem + '-' + compiler + '.d')
            flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Werror']
            run([CESTA, 'cc', '--cc', compiler, *flags, '-MMD', '-MF', dependency,
                 document, '-o', native_binary])
            run([compiler, *flags, exported, '-o', exported_binary])
            assert str(path) in dependency.read_text() and str(document) in dependency.read_text()
            for bits in range(1 << bool_depth(data[6][1])):
                expected = semantics(data, bits)
                assert run([native_binary, bits]).stdout.rstrip('\n') == expected
                assert run([exported_binary, bits]).stdout.rstrip('\n') == expected
                executions += 2
            twice = work / (stem + '-twice.c')
            twice.write_text(probe.read_text().replace(f'#include "{output.name}"',
                              f'#include "{output.name}"\n#include "{output.name}"'))
            assert 'duplicate protocol definition' in run(
                [compiler, *flags, '-fsyntax-only', twice], ok=False).stderr
            refusals += 1
        # A forged output is detected even if it is still valid C.
        output.write_text(output.read_text().replace(f"return {name}_answered;", f"return {name}_raised;", 1))
        run([CESTA, "protocol", "verify", path, "Protocols", "-o", output], ok=False)
        output.write_bytes(old)
        plan.write_text(plan.read_text().replace("(Target", "(ForgedTarget", 1))
        run([CESTA, "protocol", "verify", path, "Protocols", "-o", output, "--plan", plan], ok=False)
        refusals += 2
        checked += 1
    name, data = declaration(ROOT / "protocols/Admission.module")
    for number, (label, mutant) in enumerate(mutations(data)):
        path = work / f"refusal-{number}.module"
        path.write_text(module(name, mutant))
        verdict = run([CESTA, "protocol", "check", path, "Protocols"], ok=False)
        assert "byte" in verdict.stderr, (label, verdict.stderr)
        refusals += 1
    # The export's own definition marker must never shadow a native identifier.
    marker = Sym("CESTA_PROTOCOL_" + name + "_DEFINED")
    for number in range(3):
        collision = copy.deepcopy(data)
        if number == 0:
            collision[2][1] = marker
        elif number == 1:
            collision[3][1][2] = marker
        else:
            collision[4][1][2] = marker
        path = work / f"marker-collision-{number}.module"
        path.write_text(module(name, collision))
        run([CESTA, "protocol", "check", path, "Protocols"], ok=False)
        refusals += 1
    collision = copy.deepcopy(data)
    collision[3][1][2] = Sym("CESTA_PROTOCOL_other_DEFINED")
    path = work / "cross-marker-collision.module"
    path.write_text(module(name, collision).removesuffix("} } }\n") +
                    '(other) == (Protocol "cesta-protocol/1" (Context OtherContext) '
                    '(Hooks) (Resources) (Entry (Facts) (Owns)) (Body (Return done)));\n} } }\n')
    run([CESTA, "protocol", "check", path, "Protocols"], ok=False)
    refusals += 1
    # Empty-answer, suspension and resumed ownership are distinct valid outcomes.
    for outcome in OUTCOMES:
        simple = copy.deepcopy(data)
        simple[6][1] = [Sym("Return"), Sym(outcome)]
        path = work / f"return-{outcome}.module"
        path.write_text(module(name, simple))
        run([CESTA, "protocol", "check", path, "Protocols"])
        checked += 1
    resumed = copy.deepcopy(data)
    resumed[5][2].append(Sym("arguments"))
    resumed[5][1].append(Sym("ready"))
    resumed[6][1] = [Sym("Transfer"), Sym("arguments"), [Sym("Return"), Sym("suspended")]]
    path = work / "resume.module"
    path.write_text(module(name, resumed))
    run([CESTA, "protocol", "check", path, "Protocols"])
    checked += 1
    # Source/output alias detection happens before opening the output.
    before = path.read_bytes()
    run([CESTA, "protocol", "export", path, "Protocols", "-o", path], ok=False)
    assert path.read_bytes() == before
    same_output = str(work) + '/./aliased.h'
    run([CESTA, 'protocol', 'export', path, 'Protocols', '-o', work / 'aliased.h',
         '--plan', same_output], ok=False)
    assert not (work / 'aliased.h').exists()
    refusals += 1
    imported = work / "Imported.module"
    imported.write_text('import "resume.module" as base\nModule Imported { Theory Protocols() { base.Protocols() } }\n')
    run([CESTA, "protocol", "export", imported, "Protocols", "-o", work / "imported.h",
         "--depfile", work / "imported.d"])
    assert all(str(p) in (work / "imported.d").read_text() for p in (path, imported))
    document = work / "imported.cesta"
    document.write_text('(cesta native-c/1 compact-c/2 (unit '
                        '(use-protocols "Imported.module" Protocols)))\n')
    dependencies = work / "imported-native.d"
    run([CESTA, "cc", "-MM", "-MT", "generated.c", "-MF", dependencies, document])
    assert all(str(p) in dependencies.read_text() for p in (path, imported, document))
    # Native document export must protect both direct and transitive inputs,
    # including aliases, before opening its C output or plan.
    protected = (path, imported, document)
    before = {p: p.read_bytes() for p in protected}
    alias = work / "import-alias.module"
    alias.hardlink_to(path)
    for operand in (*protected, alias):
        run([CESTA, "export-c", document, "-o", operand], ok=False)
        run([CESTA, "export-c", document, "--plan", operand], ok=False)
        assert all(p.read_bytes() == text for p, text in before.items())
        refusals += 2
    missing_output = work / "same-new-output.c"
    run([CESTA, "export-c", document, "-o", missing_output,
         "--plan", str(work) + "/./same-new-output.c"], ok=False)
    assert not missing_output.exists()
    refusals += 1
    single_clause = copy.deepcopy(data)
    single_clause[4][1].pop()
    rejected = work / "single-resource-clause.module"
    rejected.write_text(module(name, single_clause))
    run([CESTA, "protocol", "check", rejected, "Protocols"], ok=False)
    refusals += 1
    from protocol_instrumentation import controls
    instrumented_executions, instrumented_refusals = controls(work)
    assert hashlib.sha256(CESTA.read_bytes()).hexdigest() == source_hash
    summary = dict(binary_sha256=source_hash, checked=checked, executions=executions, refusals=refusals+1,
                   instrumented_executions=instrumented_executions, instrumented_refusals=instrumented_refusals,
                   disabled_objects_identical=4)
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"protocols: {checked} accepted, {executions} source/C trace comparisons, {refusals+1} refusals; {work.name}")
    print(f"instrumentation: {instrumented_executions} native controls, {instrumented_refusals} refusals; 4 disabled objects identical")


if __name__ == "__main__":
    main()
