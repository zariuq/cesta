"""Mutable-currentness certificates, including writes that report failure."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile

from protocols import CESTA, ROOT, declaration, module, run
from oracles.pro_compact_cesta import Sym


RUNTIME = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
typedef struct {
    unsigned revision, observed, old_snapshot, value, published, calls, failures;
    unsigned bits;
} CurrentContext;
static bool current_is_current(CurrentContext *c) {
    return c->revision == c->observed;
}
static bool current_mutate(CurrentContext *c) {
    ++c->revision;
    c->value += 7;
    ++c->calls;
    return (c->bits & 1u) != 0;
}
static bool current_refresh(CurrentContext *c) {
    ++c->calls;
    if (!(c->bits & 2u)) return false;
    c->observed = c->revision;
    return true;
}
static bool current_publish(CurrentContext *c) {
    if (!current_is_current(c)) abort();
    ++c->calls;
    c->published = c->value;
    return (c->bits & 4u) != 0;
}
#define current_run_CHECKED CHECKED
#include "current.h"
int main(void) {
    for (unsigned bits = 0; bits < 8; ++bits) {
        CurrentContext c = { .revision=3, .observed=3,
            .value=11, .old_snapshot=11, .bits=bits };
        current_run_outcome outcome = current_run(&c);
        if (c.old_snapshot != 11 || c.value != 18 || c.revision != 4) return 1;
        unsigned calls = !(bits & 1u) ? 1u : !(bits & 2u) ? 2u : 3u;
        if (c.calls != calls || c.published != (calls == 3 ? 18u : 0u)) return 2;
        current_run_outcome expected = !(bits & 1u) ? current_run_raised :
            !(bits & 2u) ? current_run_engine_failure :
            !(bits & 4u) ? current_run_raised : current_run_done;
        if (outcome != expected) return 3;
    }
    return 0;
}
'''


def main():
    work = Path(tempfile.mkdtemp(prefix="protocol-certificates-", dir=ROOT / "build"))
    name, data = declaration(ROOT / "protocols/Current.module")
    run([CESTA, "protocol", "export", ROOT / "protocols/Current.module", "Protocols",
         "-o", work / "current.h"])
    (work / "runtime.c").write_text(RUNTIME)
    controls = 0
    for compiler in ("gcc", "clang"):
        for checked in (0, 1):
            for sanitized in (False, True):
                binary = work / f"{compiler}-{checked}-{sanitized}"
                flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                         f"-DCHECKED={checked}"]
                if sanitized:
                    flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                run([compiler, *flags, work / "runtime.c", "-o", binary])
                run([binary], cwd=work)
                controls += 8
    refusals = []
    for branch in (2, 3):
        bad = copy.deepcopy(data)
        bad[6][1][branch] = [Sym("Call"), Sym("publish"),
                            [Sym("Return"), Sym("done")], [Sym("Return"), Sym("raised")]]
        refusals.append((f"stale-{'true' if branch==2 else 'false'}", bad))
    duplicate = copy.deepcopy(data)
    duplicate[3][1][7].append(Sym("current"))
    refusals.append(("duplicate-invalidation", duplicate))
    malformed = copy.deepcopy(data)
    malformed[3][1][7][0] = Sym("InvalidatesTypo")
    refusals.append(("unknown-clause", malformed))
    for label, bad in refusals:
        source = work / f"{label}.module"
        source.write_text(module(name, bad))
        result = run([CESTA, "protocol", "export", source, "Protocols"], ok=False)
        assert "unestablished fact" in result.stderr or "duplicate" in result.stderr or "Invalidates" in result.stderr
    # Forgetting a certificate does not assert that its predicate became false.
    forget = copy.deepcopy(data)
    forget[3] = [Sym("Hooks"), [Sym("Hook"), Sym("forget"), Sym("current_refresh"),
        Sym("guard"), [Sym("Requires")], [Sym("Uses")], [Sym("Proves")],
        [Sym("Invalidates"), Sym("current")]]]
    forget[6][1] = [Sym("Call"), Sym("forget"), [Sym("Return"), Sym("done")], [Sym("Return"), Sym("done")]]
    path = work / "forget.module"
    path.write_text(module(name, forget))
    run([CESTA, "protocol", "export", path, "Protocols", "-o", work / "current.h"])
    # Refresh can leave the entry predicate true; there is no required false check.
    text = (work / "current.h").read_text()
    assert '"fact", "current", false' not in text
    summary = {"compiler_sha256": hashlib.sha256(CESTA.read_bytes()).hexdigest(),
               "runtime_controls": controls, "refusals": len(refusals)}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "build/protocol-certificates-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"protocol certificates: {controls} changing-state controls, {len(refusals)} refusals; GCC/Clang, checked/plain, ASan/UBSan")


if __name__ == "__main__":
    main()
