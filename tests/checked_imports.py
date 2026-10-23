"""Checked module interfaces, ownership transfer and incremental dependencies."""

import hashlib
import json
from pathlib import Path
import tempfile

from checked_core import BINARY, ROOT, run

OWNED = '''(cesta checked-c/1 (module owned_client) (failure owned_client_failure)
  (allocator owners_allocate owners_release)
  (import "lib/Owners.cesta" as Owners)
  (fn consume (params (bytes own)) (returns usize) (effects heap)
    (body (return (call Owners_consume bytes))))
  (fn forward (params (bytes own)) (returns own) (effects)
    (body (return (call Owners_identity bytes)))))
'''

RUNTIME = r'''
#include "Pipeline.h"
#include "Owned.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

static size_t live;
void pipeline_failure(void) { _Exit(71); }
void packed_words_failure(void) { _Exit(71); }
void format_bytes_failure(void) { _Exit(71); }
void byte_views_failure(void) { _Exit(71); }
void owned_client_failure(void) { _Exit(71); }
void owners_failure(void) { _Exit(71); }
void *owners_allocate(size_t length) { void *p = malloc(length); if (p) ++live; return p; }
void owners_release(void *p) { assert(live); --live; free(p); }

int main(void)
{
    const uint8_t data[] = {1, 2, 3, 4};
    uint8_t output[32] = {0};
    cesta_read_bytes packet = {data, sizeof data};
    cesta_write_bytes destination = {output, sizeof output};
    assert(client_pipeline_decimal_word(packet, destination) == 8);
    assert(!memcmp(output, "67305985", 8));
    cesta_read_bytes prefix = client_pipeline_prefix(packet, 2);
    assert(prefix.data == data && prefix.len == 2);
    cesta_owned_bytes allocated = {owners_allocate(7), 7};
    assert(allocated.data && live == 1);
    assert(owned_client_consume(allocated) == 7 && live == 0);
    allocated = (cesta_owned_bytes){owners_allocate(9), 9};
    assert(allocated.data && live == 1);
    cesta_owned_bytes returned = owned_client_forward(allocated);
    assert(returned.data == allocated.data && returned.len == 9 && live == 1);
    owners_release(returned.data);
    assert(live == 0);
    return 0;
}
'''


def main():
    work = Path(tempfile.mkdtemp(prefix="checked-imports-", dir=ROOT / "build"))
    library = work / "lib"
    library.mkdir()
    for name in ("PackedWords", "FormatBytes", "ByteViews", "Owners"):
        (library / (name + ".cesta")).write_bytes((ROOT / "checked/examples" / (name + ".cesta")).read_bytes())
    pipeline = work / "ClientPipeline.cesta"
    text = (ROOT / "checked/examples/ClientPipeline.cesta").read_text()
    for name in ("PackedWords", "FormatBytes", "ByteViews"):
        text = text.replace('"' + name + '.cesta"', '"lib/' + name + '.cesta"')
    pipeline.write_text(text)
    owned = work / "Owned.cesta"
    owned.write_text(OWNED)
    run([BINARY, "checked", "header", pipeline, "-o", work / "Pipeline.h"])
    run([BINARY, "checked", "header", owned, "-o", work / "Owned.h"])
    (work / "host.c").write_text(RUNTIME)
    plan = run([BINARY, "checked", "plan", pipeline]).stdout
    exported = work / "exported.c"
    record = work / "exported.plan"
    run([BINARY, "export-c", "--plan", record, pipeline, "-o", exported, "--"])
    assert record.read_bytes() == plan, "the actual C export has a different permission plan"
    direct = work / "owned-export.plan"
    run([BINARY, "export-c", "--plan", direct, owned, "--"])
    assert direct.read_bytes() == run([BINARY, "checked", "plan", owned]).stdout
    # A module without imports must record its actual export as well.
    run([BINARY, "export-c", "--plan", direct, library / "PackedWords.cesta", "--"])
    assert direct.read_bytes() == run([BINARY, "checked", "plan", library / "PackedWords.cesta"]).stdout

    sources = [pipeline, owned, *sorted(library.glob("*.cesta")), work / "host.c"]
    for compiler in ("gcc", "clang"):
        for optimization in ("-O0", "-O2"):
            executable = work / (compiler + optimization)
            run([BINARY, "cc", "--cc", compiler, "-std=c11", optimization, "-Wall", "-Wextra",
                 "-Wpedantic", "-Werror", "-I", work, *sources, "-o", executable])
            run([executable], cwd=work)
        run([BINARY, "cc", "--cc", compiler, "-std=c11", "-MMD", "-MP", "-c", pipeline,
             "-o", work / (compiler + ".o")])
        dependencies = (work / (compiler + ".d")).read_text()
        for name in ("PackedWords", "FormatBytes", "ByteViews"):
            assert name + ".cesta" in dependencies, dependencies
        assert "cesta-generated" not in dependencies
        # Generated implementation names are protected against CPP capture.
        poison = run([BINARY, "cc", "--cc", compiler, "-Dcesta_result=other_result", "-c", pipeline,
                      "-o", work / "poison.o"], success=False)
        assert b"preprocessor macro" in poison.stderr
        print(compiler + ": checked calls, borrowed/owned returns, actual export plans, dependencies and CPP refusal passed", flush=True)

    manifest = work / "cesta.project"
    manifest.write_text('''(cesta project/1 (out "out")
      (profile local (flags "-std=c11" "-O2" "-Wall" "-Wextra" "-Wpedantic" "-Werror"))
      (target client (profile local) (sources "ClientPipeline.cesta" "Owned.cesta" "lib/*.cesta" "host.c") (include ".")))''')
    for compiler in ("gcc", "clang"):
        command = [BINARY, "build", "--cc", compiler, "-j4"]
        run(command, cwd=work)
        run([work / "out/client/client"], cwd=work)
        object_file = work / "out/client/obj/ClientPipeline.cesta.o"
        before = object_file.stat().st_mtime_ns
        changed = library / "PackedWords.cesta"
        updated = changed.read_text().replace("(module packed_words)", "(module packed_words_v2)")
        if updated == changed.read_text():
            updated = changed.read_text().replace("(module packed_words_v2)", "(module packed_words)")
        changed.write_text(updated)
        run(command, cwd=work)
        run([work / "out/client/client"], cwd=work)
        assert object_file.stat().st_mtime_ns > before, "a changed imported interface did not rebuild its caller"
        run([*command, "--export-c", "exported-" + compiler], cwd=work)
        directory = work / ("exported-" + compiler)
        run(["make", "-s", "-j4", "CC=" + compiler], cwd=directory)
        run([directory / "out/client/client"], cwd=directory)

    failures = {
        "different_allocators": (OWNED.replace("(allocator owners_allocate owners_release)", "(allocator other_allocate other_release)"), b"same allocator"),
        "missing_module": (text.replace("lib/PackedWords.cesta", "absent.cesta"), b"cannot open"),
        "duplicate_prefix": (text.replace("as Format", "as Words"), b"duplicate checked import prefix"),
        "word_width": (text.replace("(module client_pipeline)", "(module client_pipeline) (word-bits 32)"), b"word-bits differs"),
        "colliding_alias": (text.replace("(fn decimal_word", "(fn Words_load_le64"), b"collides with an existing function"),
    }
    for name, (source, message) in failures.items():
        path = work / (name + ".cesta")
        path.write_text(source)
        result = run([BINARY, "checked", "check", path], success=False)
        assert b"error:" in result.stderr and b"| " in result.stderr and message in result.stderr, result.stderr
    a, b = work / "cycle_a.cesta", work / "cycle_b.cesta"
    for first, second in ((a, b), (b, a)):
        first.write_text(f'(cesta checked-c/1 (module loop) (failure fail) (import "{second.name}" as Cycle))\n')
    result = run([BINARY, "checked", "check", a], success=False)
    assert b"import cycle" in result.stderr
    dependency = library / "PackedWords.cesta"
    before = dependency.read_bytes()
    for command in ([BINARY, "checked", "header", pipeline, "-o", dependency],
                    [BINARY, "export-c", pipeline, "-o", dependency, "--"],
                    [BINARY, "export-c", "--plan", dependency, pipeline, "--"]):
        run(command, success=False)
        assert dependency.read_bytes() == before
    summary = {"binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(), "work": str(work),
               "native_compilers": ["gcc", "clang"], "cross_module_ownership": True,
               "actual_export_plan_equal": True, "incremental_import_rebuilds": True,
               "refusals": [*failures, "cycle", "header-alias", "C-alias", "plan-alias", "CPP-capture"]}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "build/checked-imports-summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
