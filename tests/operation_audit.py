"""Operation facts in CeTTa Prime: every place a native operation's facts are written, read at a pinned
revision, joined by spelling, with the places that disagree.

The files are taken from the pinned revision with git show into build/operation-audit; the owner's
working tree is never read. The X-macro tables (symbol rows, capability lists, query effects, the PeTTa
registry) and the builtin policies are enumerated by compiling probe units against those files, so the
compiler reads them. Demand rules, the worker barrier, the replay refusals, the Prime native spellings
and the main.c type lists sit inside functions and are read as text; the report labels them so. Types
come from lib/stdlib.metta through a small S-expression reader.

The output is a report (summary.json, report.txt) and a draft declaration (operations.module) with one
contract per operation: imported facts filled in, the facts no source states marked Unknown."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
CETTA = ROOT.parent / "hyperon/cetta-prime-weights-parallel-20261001"
REVISION = "68153eae"
WORK = ROOT / "build/operation-audit"
FILES = ["src/symbol.h", "src/scalar_trie.h", "src/generated/cetta_execution_contracts.generated.h",
         "src/petta_builtin_registry.def", "src/petta_builtin_registry.h", "src/session.c", "src/session.h", "src/lang.c", "src/lang.h",
         "src/eval.c", "src/prime_replay_pure.inc", "src/main.c", "lib/stdlib.metta"]
# Build configuration the probes assume; recorded in the summary.
DEFINES = {"CETTA_BUILD_WITH_GMP": "0", "CETTA_BUILD_WITH_PETTA_TYPECHECK_V2": "0"}
EFFECT_BITS = {1: "relational-query", 2: "world-write", 3: "relational-query world-write"}

TABLES = r'''#include <stdio.h>
#include "symbol.h"
#include "query_rows.h"
int main(void) {
#define S(field, text) printf("symbol\t%s\t%s\n", #field, text);
    CETTA_BUILTIN_SYMBOLS(S)
#define STATIC(field) printf("static\t%s\n", #field);
    CETTA_STATIC_GROUNDED_SYMBOL_FIELDS(STATIC)
#define PURE(field) printf("pure\t%s\n", #field);
    CETTA_PURE_GROUNDED_SYMBOL_FIELDS(PURE)
#define TYPE_PURE(field) printf("type-pure\t%s\n", #field);
    CETTA_TYPE_PURE_GROUNDED_SYMBOL_FIELDS(TYPE_PURE)
#define SPECULATIVE(field) printf("speculative\t%s\n", #field);
    CETTA_SPECULATIVE_SYMBOL_FIELDS(SPECULATIVE)
#define QUERY(field, effect) printf("query\t%s\t%u\n", #field, (unsigned)(effect));
    CETTA_GSLT_QUERY_EFFECT_HEAD_ROWS(QUERY)
#define TOTAL(field, arity) printf("total-integer\t%s\t%u\n", #field, (unsigned)(arity));
    CETTA_GSLT_TOTAL_INTEGER_HEAD_ROWS(TOTAL)
    return 0;
}
'''

REGISTRY = r'''#include <stdint.h>
#include <stdio.h>
@VARIADIC@
int main(void) {
#define PETTA_BUILTIN(ordinal, name, arity, impl, evaluation, signature, grade, a, b, out, contextual, rule, flat, lowering) \
    printf("registry\t%d\t%s\t%d\t%s\t%s\t%s\n", ordinal, name, arity, #impl, #evaluation, #signature);
#include "petta_builtin_registry.def"
    return 0;
}
'''

POLICIES = r'''#include "session.c"
int main(void) {
    for (size_t i = 0; i < sizeof CETTA_BUILTIN_POLICIES / sizeof *CETTA_BUILTIN_POLICIES; ++i)
        printf("policy\t%s\t%u\t%s\n", CETTA_BUILTIN_POLICIES[i].name,
               (unsigned)CETTA_BUILTIN_POLICIES[i].visibility_mask,
               CETTA_BUILTIN_POLICIES[i].builtin_classification);
    return 0;
}
'''


def pin(revision):
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "src/generated").mkdir(parents=True)
    (WORK / "lib").mkdir()
    digests = {}
    for path in FILES:
        data = subprocess.run(["git", "-C", str(CETTA), "show", f"{revision}:{path}"], capture_output=True,
                              check=True).stdout
        (WORK / path).write_bytes(data)
        digests[path] = hashlib.sha256(data).hexdigest()
    return digests


def macro_block(text, name):
    """A #define with its continuation lines, verbatim."""
    start = text.index(f"#define {name}")
    end = start
    while True:
        end = text.index("\n", end)
        if text[end - 1] != "\\":
            return text[start:end + 1]
        end += 1


def query_rows_header():
    """The query-effect enum and the row macros of the generated contracts header, verbatim; the rest of
    that header declares functions over runtime types the probe does not need."""
    text = (WORK / "src/generated/cetta_execution_contracts.generated.h").read_text()
    enum = text[text.index("typedef enum {"):text.index("} CettaGsltQueryEffect;") + len("} CettaGsltQueryEffect;")]
    blocks = [macro_block(text, n) for n in ("CETTA_GSLT_QUERY_EFFECT_HEAD_ROWS(X)", "CETTA_GSLT_TOTAL_INTEGER_HEAD_ROWS(X)")]
    (WORK / "src/query_rows.h").write_text(enum + "\n" + "".join(blocks))


def probe(name, text, extra=()):
    source = WORK / "src" / f"{name}.c"
    source.write_text(text)
    exe = WORK / name
    flags = [f"-D{k}={v}" for k, v in DEFINES.items()]
    subprocess.run(["cc", "-std=gnu11", "-w", *flags, "-I", str(WORK / "src"), str(source), *extra, "-o", str(exe)],
                   check=True)
    lines = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.splitlines()
    return [line.split("\t") for line in lines]


# ---- S-expressions, for the MeTTa type declarations -------------------------

def tokens(text):
    i = 0
    while i < len(text):
        c = text[i]
        if c == ";":
            while i < len(text) and text[i] != "\n":
                i += 1
        elif c.isspace():
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            yield text[i:j + 1]
            i = j + 1
        else:
            j = i
            while j < len(text) and not text[j].isspace() and text[j] not in '();"':
                j += 1
            yield text[i:j]
            i = j


def forms(text):
    stack, out = [[]], []
    for t in tokens(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) == 1:
                continue
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t)
    return stack[0]


def type_arities(text):
    found = {}
    for f in forms(text):
        if isinstance(f, list) and len(f) == 3 and f[0] == ":" and isinstance(f[1], str):
            t = f[2]
            arity = len(t) - 2 if isinstance(t, list) and t and t[0] == "->" else 0
            found.setdefault(f[1], set()).add(arity)
    return found


# ---- Text reads inside functions --------------------------------------------

def function_text(source, signature):
    """The definition of the function, skipping prototypes."""
    start = source.index(signature)
    while source.find(";", start) < source.find("{", start):
        start = source.index(signature, start + 1)
    depth, i = 0, source.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
        if depth == 0:
            return source[start:i]


def demand_rules(source):
    rules = []
    for m in re.finditer(r"PRIME_DEMAND_RULE\(\s*g_builtin_syms\.(\w+),\s*(\w+),\s*(\w+),\s*(\w+),\s*(\w+),\s*"
                         r"(PRIME_NEED_ARGUMENT_\w+)\)", source):
        rules.append({"field": m[1], "min": m[2], "max": m[3], "mode": m[6].replace("PRIME_NEED_ARGUMENT_", ""),
                      "line": source.count("\n", 0, m.start()) + 1})
    for m in re.finditer(r"PRIME_NUMERIC_DEMAND\(\s*(\w+)\s*\)", source):
        if m[1] != "h":
            rules.append({"field": m[1], "min": "0", "max": "any", "mode": "WHNF",
                          "line": source.count("\n", 0, m.start()) + 1})
    return rules


def gather(revision):
    """Every operation's facts at the revision, with the lists as the runtime declares them."""
    digests = pin(revision)
    query_rows_header()
    rows = probe("tables", TABLES)
    header = (WORK / "src/petta_builtin_registry.h").read_text()
    variadic = re.search(r"enum \{ PETTA_BUILTIN_VARIADIC = [^}]*\};", header)[0]
    registry = probe("registry", REGISTRY.replace("@VARIADIC@", variadic))
    policies = probe("policies", POLICIES, [str(WORK / "src/lang.c")])
    eval_source = (WORK / "src/eval.c").read_text(errors="replace")
    replay_source = (WORK / "src/prime_replay_pure.inc").read_text(errors="replace")
    main_source = (WORK / "src/main.c").read_text(errors="replace")

    spelling = {r[1]: r[2] for r in rows if r[0] == "symbol"}
    field_of = {v: k for k, v in spelling.items()}
    lists = {kind: {r[1] for r in rows if r[0] == kind} for kind in ("static", "pure", "type-pure", "speculative")}
    query = {r[1]: int(r[2]) for r in rows if r[0] == "query"}
    total = {r[1]: int(r[2]) for r in rows if r[0] == "total-integer"}
    # Prime's own operations: the heads its call-form predicates admit, with their arities.
    interned = {m[1]: m[2] for m in re.finditer(r'ids\.(\w+) = symbol_intern_cstr\(g_symbols, "([^"]+)"\)',
                                                 eval_source)}
    prime_forms = {}
    control = function_text(eval_source, "static bool prime_need_is_explicit_control_form(")
    for case in re.finditer(r"case (\d+)u:(.*?)(?=case \d+u:|default:|\Z)", control, re.S):
        for ident in re.findall(r"syms->(\w+)", case[2]):
            prime_forms.setdefault(ident, set()).add(int(case[1]) - 1)
    interaction = function_text(eval_source, "static bool prime_need_interaction_arity(SymbolId head,CettaExprLen nargs)")
    for clause in re.finditer(r"nargs==(\d+)u(.*?)(?=nargs==|\Z)", interaction, re.S):
        for ident in re.findall(r"syms->(\w+)", clause[2]):
            prime_forms.setdefault(ident, set()).add(int(clause[1]))
    prime_native = {interned[i]: i for i in prime_forms if i in interned}
    barrier_text = function_text(eval_source, "static bool prime_native_worker_call_local(")
    barrier_text = "\n".join(line for line in barrier_text.splitlines() if "#define" not in line)
    barrier = sorted(set(re.findall(r"g_builtin_syms\.(\w+)", barrier_text)))
    replay_ids = sorted(set(re.findall(r"ids->(\w+)", replay_source)))
    # Forms the replay scan passes through, by head and argument count; every other builtin is refused.
    replay_forms = {}
    for m in re.finditer(r"h==(?:g_builtin_syms\.|ids->)(\w+)&&n==(\d+)u", replay_source):
        replay_forms.setdefault(m[1], set()).add(int(m[2]))
    demand = demand_rules(eval_source)
    types = type_arities((WORK / "lib/stdlib.metta").read_text(errors="replace"))
    registry_arities = {}
    for r in registry:
        registry_arities.setdefault(r[2], set()).add(int(r[3]))
    policy = {r[1]: (int(r[2]), r[3]) for r in policies}
    arith_lists = re.findall(r"static const char \*arith_ops_(\w+)\[\] = \{([^}]*)\}", main_source)

    # One record per operation spelling.
    operations = {}
    for field, text in spelling.items():
        operations[text] = {"field": field, "in_symbol_table": True}
    for text, ident in prime_native.items():
        operations.setdefault(text, {"field": ident, "in_symbol_table": False})
        operations[text]["prime_arities"] = sorted(prime_forms[ident])
    for text, op in operations.items():
        f = op["field"]
        op["static"] = f in lists["static"]
        op["pure"] = f in lists["pure"]
        op["type_pure"] = f in lists["type-pure"]
        op["speculative"] = f in lists["speculative"] or f in lists["pure"]
        op["query_effect"] = EFFECT_BITS.get(query.get(f, 0), "pure (default)") if f in query else "pure (default)"
        op["query_row"] = f in query
        op["demand"] = [d for d in demand if d["field"] == f]
        op["registry_arities"] = sorted(registry_arities.get(text, []))
        op["type_arities"] = sorted(types.get(text, []))
        op["policy"] = policy.get(text)
        op["worker_barrier"] = op["query_row"] or f in barrier
        op["replay_listed"] = f in replay_ids
        op["replay_forms"] = sorted(replay_forms.get(f, []))
        op["speculative_listed"] = f in lists["speculative"]
        op["query_bits"] = query.get(f, 0)

    return {"digests": digests, "operations": operations, "lists": lists, "query": query, "demand": demand,
            "barrier": barrier, "replay_ids": replay_ids, "interned": interned, "spelling": spelling,
            "arith_lists": arith_lists, "registry": registry, "policies": policies, "prime_native": prime_native}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default=REVISION)
    args = parser.parse_args()
    facts = gather(args.revision)
    digests, operations, lists, query = facts["digests"], facts["operations"], facts["lists"], facts["query"]
    demand, barrier, replay_ids, interned = facts["demand"], facts["barrier"], facts["replay_ids"], facts["interned"]
    spelling, arith_lists, registry = facts["spelling"], facts["arith_lists"], facts["registry"]
    policies, prime_native = facts["policies"], facts["prime_native"]
    # The operations: names something calls, by any of the places that state call facts.
    for o in operations.values():
        o["operation"] = bool(o["static"] or o["pure"] or o["speculative"] or o["query_row"] or o["demand"]
                              or o["registry_arities"] or o["type_arities"] or o["policy"]
                              or o.get("prime_arities"))
    calls = {t: o for t, o in operations.items() if o["operation"]}

    findings = {}
    # 1. Operations neither PURE-listed nor given an effect row: the query analysis treats them as
    #    pure by default, while the PURE permission list does not admit them. Grouped for the owner.
    unclassified = {t: o for t, o in calls.items() if not o["pure"] and not o["query_row"]}
    findings["no_effect_classification"] = sorted(unclassified)
    # 2. Of those, operations the speculation list admits beyond PURE. Control forms are pure at their
    #    head; others (size, count-atoms, the _minimal-space folds) read a space, which speculation
    #    admits and the query analysis does not see.
    findings["speculation_admitted_without_pure_or_row"] = sorted(
        t for t, o in unclassified.items() if o["speculative"])
    # 3. Prime operations without a symbol row: the effect generator cannot give them a row.
    findings["prime_operation_without_symbol_row"] = sorted(
        t for t, o in calls.items() if not o["in_symbol_table"])
    # 4. Arity sets that differ between the HE stdlib types and the PeTTa registry (two dialects: the
    #    difference may be intended).
    arity = []
    for t, o in sorted(calls.items()):
        ty, reg = set(o["type_arities"]), set(o["registry_arities"])
        if ty and reg and ty != reg:
            arity.append({"operation": t, "stdlib_types": sorted(ty), "petta_registry": sorted(reg)})
    findings["arity_stdlib_vs_petta_registry"] = arity
    # 5. Duplicate demand rows.
    seen, duplicates = {}, []
    for d in demand:
        key = (d["field"], d["min"], d["max"], d["mode"])
        if key in seen:
            duplicates.append({"field": d["field"], "lines": [seen[key], d["line"]]})
        seen[key] = d["line"]
    findings["duplicate_demand_rows"] = duplicates
    # 6. Laws the capability lists keep today (each list should stay empty).
    findings["law_pure_not_type_pure"] = sorted(t for t, o in calls.items() if o["pure"] and not o["type_pure"])
    findings["law_pure_with_effect_row"] = sorted(t for t, o in calls.items() if o["pure"] and o["query_row"])
    findings["law_writer_speculative"] = sorted(
        t for t, o in calls.items() if query.get(o["field"], 0) & 2 and o["speculative"])
    # 7. Facts written as code, outside every table.
    findings["worker_barrier_written_as_code"] = [spelling.get(b, b) for b in barrier]
    findings["replay_refusal_written_as_code"] = [interned.get(i, i) for i in replay_ids]
    findings["main_c_arithmetic_type_lists"] = [{"name": n, "spellings": [x.strip() for x in v.split(",")]}
                                                for n, v in arith_lists]
    # Informational: readers the speculation list does not admit yet (it admits extensions once audited).
    findings["info_readers_not_yet_speculative"] = sorted(
        t for t, o in calls.items() if query.get(o["field"], 0) & 1 and not o["speculative"])

    # Known gaps the audit must find.
    assert "work-take" in findings["no_effect_classification"], "control: work-take has no effect classification"
    assert "size" in findings["speculation_admitted_without_pure_or_row"], "control: size reads a space, no effect row"
    assert "space-push" in findings["no_effect_classification"], "control: space-push mutates a space, no effect row"
    assert "history:record" in findings["prime_operation_without_symbol_row"], "control: history:record has no symbol row"
    assert not (findings["law_pure_not_type_pure"] or findings["law_pure_with_effect_row"]
                or findings["law_writer_speculative"]), "capability-list laws"

    # The draft declaration: imported facts, and Unknown for what no source states.
    lines = ["-- Drafted by tests/operation_audit.py from CeTTa Prime at " + args.revision + ".",
             "-- Imported facts are filled in; facts no source states are Unknown.",
             "Module PrimeOperations {", "  Theory Operations() {", "    Empty Equations {"]
    for t, o in sorted(calls.items()):
        name = re.sub(r"\W", "_", o["field"])
        forms_text = " ".join(f"(Arity {a})" for a in sorted(set(o["type_arities"]) | set(o["registry_arities"])
                                                             | set(o.get("prime_arities", [])))) or "(Unknown arity)"
        footprint = {"relational-query": "(Reads space)", "world-write": "(Writes world)",
                     "relational-query world-write": "(Reads space) (Writes world)"}.get(o["query_effect"], None)
        footprint = "(Footprint)" if o["pure"] else f"(Footprint {footprint})" if footprint else "(Unknown footprint)"
        declared = " ".join(f"({k} {'yes' if o[v] else 'no'})" for k, v in
                            (("Static", "static"), ("TypePure", "type_pure"), ("Speculative", "speculative")))
        lines.append(f'      ({name}) == (Operation "cesta-operation/1" (Spelling "{t}") (Symbol {o["field"]})')
        lines.append(f"        (Forms {forms_text}) {footprint}")
        replay = (f"(Replay (Through {' '.join(f'(Arity {a})' for a in o['replay_forms'])}) (Otherwise refused))"
                  if o["replay_forms"] else "(Replay refused)" if o["in_symbol_table"] or o["replay_listed"]
                  else "(Unknown replay)")
        lines.append(f"        (Declared {declared} {replay})")
        lines.append("        (Unknown ownership) (Unknown progress));")
    lines += ["    }", "  }", "}"]
    (WORK / "operations.module").write_text("\n".join(lines) + "\n")

    summary = {"revision": args.revision, "files": digests, "defines": DEFINES,
               "evidence": {"compiler": ["symbol rows", "capability lists", "query effects", "total-integer rows",
                                         "PeTTa registry", "builtin policies"],
                            "text": ["demand rules", "worker barrier", "replay refusals", "Prime native spellings",
                                     "main.c arithmetic lists"],
                            "metta": ["stdlib types"]},
               "counts": {"operations": len(calls), "symbol_rows": len(spelling),
                          "query_rows": len(query), "registry_rows": len(registry), "policies": len(policies),
                          "demand_rules": len(demand), "prime_native": len(prime_native),
                          **{k: len(v) for k, v in lists.items()}},
               "findings": findings, "operations": operations}
    (WORK / "summary.json").write_text(json.dumps(summary, indent=1, default=sorted) + "\n")
    report = [f"Operation facts in CeTTa Prime at {args.revision}: {len(calls)} operations "
              f"({len(spelling)} symbol rows, {len(prime_native)} Prime native spellings)."]
    for k, v in findings.items():
        report.append(f"\n{k}: {len(v)}")
        for item in v[:60]:
            report.append(f"  {item}")
    (WORK / "report.txt").write_text("\n".join(report) + "\n")
    print(f"operation audit: {len(calls)} operations at CeTTa {args.revision}; "
          + "; ".join(f"{k} {len(v)}" for k, v in findings.items()))


if __name__ == "__main__":
    main()
