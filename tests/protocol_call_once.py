"""Shared call effects and registered wake sources across repeated readers."""
import copy
import json
from pathlib import Path
import tempfile

from protocols import CESTA, ROOT, declaration, module, run
from oracles.pro_compact_cesta import Sym


def main():
    work = Path(tempfile.mkdtemp(prefix="protocol-once-", dir=ROOT / "build"))
    source = ROOT / "protocols/CallOnce.module"
    name, data = declaration(source)
    run([CESTA, "protocol", "export", source, "Protocols", "-o", work / "once.h"])
    count = 0
    for compiler in ("gcc", "clang"):
        for checked in (0, 1):
            for sanitized in (False, True):
                flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                         f"-DCHECKED={checked}", "-I", work]
                if sanitized:
                    flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                binary = work / f"{compiler}-{checked}-{sanitized}"
                run([compiler, *flags, ROOT / "tests/protocol_call_once/host.c", "-o", binary])
                run([binary], cwd=work)
                dishonest = run([binary, "dishonest"], cwd=work, ok=not checked)
                assert dishonest.returncode == (83 if checked else 0)
                count += 128 * 4
    mutations = []
    bare = copy.deepcopy(data)
    bare[6][1] = [Sym("Return"), Sym("suspended")]
    mutations.append(("no-wake", bare))
    premature = copy.deepcopy(data)
    premature[6][1] = [Sym("Return"), Sym("answered")]
    mutations.append(("no-result", premature))
    for branch in (2, 3):
        repeated = copy.deepcopy(data)
        perform = repeated[6][1][3][2]
        assert perform[:2] == [Sym("Call"), Sym("perform")]
        perform[branch] = [Sym("Call"), Sym("perform"),
                           [Sym("Return"), Sym("answered")], [Sym("Return"), Sym("resource_failure")]]
        mutations.append((f"repeated-effect-{branch}", repeated))
    stale_wake = copy.deepcopy(data)
    # A later mutation cannot keep using an earlier wake certificate.
    stale_wake[3][5].append([Sym("Invalidates"), Sym("wake_registered")])
    park = stale_wake[6][1][3][3][3]
    assert park[:2] == [Sym("Call"), Sym("park")]
    park[2] = [Sym("Call"), Sym("park"), [Sym("Return"), Sym("suspended")],
               [Sym("Return"), Sym("suspended")]]
    mutations.append(("invalidated-wake", stale_wake))
    for label, bad in mutations:
        path = work / (label + ".module")
        path.write_text(module(name, bad))
        result = run([CESTA, "protocol", "check", path, "Protocols"], ok=False)
        assert "unestablished" in result.stderr, (label, result.stderr)
    (ROOT / "build/protocol-call-once-summary.json").write_text(json.dumps({
        "scenarios": count, "refusals": len(mutations),
        "dishonest_hook_controls": 8}, indent=2) + "\n")
    print(f"call obligations: {count} shared-call scenarios, 5 refusals, "
          "8 dishonest-hook controls; GCC/Clang, checked/plain, ASan/UBSan")


if __name__ == "__main__":
    main()
