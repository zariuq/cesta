"""Typed byte views, affine owners and readable native lowering."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(args, cwd=ROOT, success=True):
    result = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError((args, result.returncode, result.stdout.decode(errors="replace"),
                              result.stderr.decode(errors="replace")))
    return result


def module(body, result="unit", params="", effects="", declarations=""):
    return f"""(cesta checked-c/1 (module controls) (failure checked_failure)
      (allocator checked_allocate checked_release)
      {declarations}
      (fn test (params {params}) (returns {result}) (effects {effects}) (body {body})))
"""


MORE = """(cesta checked-c/1
 (module extra) (failure owners_failure) (allocator owners_allocate owners_release)
 (fn first_byte (params (bytes own)) (returns u8) (effects heap)
   (body (return (at bytes (usize 0)))))
 (fn returned_read (params (bytes read)) (returns read from bytes) (effects)
   (body (borrow copy read bytes) (return copy)))
 (fn sum_twice (params (left read) (right read)) (returns u64) (effects)
   (body (return (wrap-add (as u64 (at left (usize 0))) (as u64 (at right (usize 0)))))))
 (fn aliases_read (params) (returns u64) (effects)
   (body (array bytes 2) (put bytes (usize 0) (u8 13))
     (return (call sum_twice bytes bytes))))
 (fn return_borrow (params) (returns u8) (effects)
   (body (array bytes 2) (put bytes (usize 0) (u8 29))
     (let view read (call returned_read bytes))
     (let answer u8 (at view (usize 0))) (end view) (return answer)))
 (fn allocation_first (params (count usize)) (returns u8) (effects heap)
   (body (allocate bytes count
     (then (put bytes (usize 0) (u8 91)) (return (call first_byte bytes)))
     (else (return (u8 0))))))
 (fn nested (params) (returns usize) (effects heap)
   (body (allocate outer (usize 3)
     (then (allocate inner (usize 5)
       (then (return (add (len outer) (len inner))))
       (else (return (usize 3)))))
     (else (return (usize 0))))))
 (fn loop_cleanup (params (count usize)) (returns usize) (effects heap)
   (body (var index usize (usize 0))
     (while (lt index count)
       (body (allocate bytes (usize 4)
         (then (set index (add index (usize 1)))
           (if (eq index (usize 2)) (then (break)) (else)))
         (else (return (usize 0))))))
     (return index)))
 (fn loop_continue (params (count usize)) (returns usize) (effects heap)
   (body (var index usize (usize 0))
     (while (lt index count)
       (body (allocate bytes (usize 4)
         (then (set index (add index (usize 1)))
           (if (eq index (usize 2)) (then (continue)) (else))
           (put bytes (usize 0) (u8 7)))
         (else (return (usize 0))))))
     (return index)))
 (fn returned_write (params (bytes write)) (returns write from bytes) (effects)
   (body (borrow view write bytes) (return view)))
 (fn write_return (params) (returns u8) (effects)
   (body (array bytes 2) (let view write (call returned_write bytes))
     (borrow child write view) (put child (usize 0) (u8 41)) (end child)
     (put view (usize 1) (u8 3)) (end view)
     (return (at bytes (usize 0)))))
 (fn copied_parameter (params (bytes read)) (returns read from bytes) (effects)
   (body (copy retained bytes) (end bytes) (return retained)))
 (fn bool_order (params (first u8) (second u8)) (returns bool) (effects)
   (body (return (and (lt first second) (or (eq first (u8 1)) (eq second (u8 4)))))))
 (fn wrap_product (params) (returns u16) (effects)
   (body (return (wrap-mul (u16 65535) (u16 65535)))))
 (fn zeroed (params) (returns u8) (effects heap)
   (body (allocate bytes (usize 1)
     (then (return (at bytes (usize 0)))) (else (return (u8 99))))))
 (fn bounds (params) (returns u8) (effects)
   (body (array bytes 0) (return (at bytes (usize 0)))))
 (fn overflow (params) (returns u8) (effects)
   (body (return (add (u8 255) (u8 1)))))
 (fn bad_shift (params) (returns u64) (effects)
   (body (return (shl (u64 1) (usize 64)))))
 (fn zero_divisor (params) (returns u64) (effects)
   (body (return (div (u64 1) (u64 0)))))
 (fn required (params) (returns u8) (effects)
   (body (require false) (return (u8 0))))
)
"""


RUNTIME = r"""
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "ByteViews.h"
#include "Owners.h"
#include "Extra.h"

static size_t allocations, releases, attempts, fail_at;
static size_t sizes[64], freed[64];
static void *addresses[64];
void byte_views_failure(void) { _Exit(73); }
void owners_failure(void) { _Exit(73); }
void *owners_allocate(size_t size)
{
    ++attempts;
    if (fail_at && attempts == fail_at) return NULL;
    void *p = malloc(size);
    assert(p);
    memset(p, 0xa5, size);
    addresses[allocations] = p;
    sizes[allocations++] = size;
    return p;
}
void owners_release(void *p)
{
    assert(p);
    size_t i = 0;
    while (i < allocations && addresses[i] != p) ++i;
    assert(i < allocations);
    freed[releases++] = sizes[i];
    addresses[i] = NULL;
    free(p);
}
static void reset(void)
{
    allocations = releases = attempts = fail_at = 0;
    memset(addresses, 0, sizeof addresses);
}
int main(int argc, char **argv)
{
    if (argc > 1) {
        if (!strcmp(argv[1], "bounds")) return extra_bounds();
        if (!strcmp(argv[1], "overflow")) return extra_overflow();
        if (!strcmp(argv[1], "shift")) return (int)extra_bad_shift();
        if (!strcmp(argv[1], "division")) return (int)extra_zero_divisor();
        if (!strcmp(argv[1], "require")) return extra_required();
        return 2;
    }
    assert(byte_views_local_checksum() == 1040);
    assert(byte_views_reborrow() == 7);
    const uint8_t input[] = {2, 3, 5};
    cesta_read_bytes view = {input, 3};
    assert(byte_views_checksum(view) == 10);
    cesta_read_bytes prefix = byte_views_prefix(view, 2);
    assert(prefix.data == input && prefix.len == 2);
    cesta_read_bytes empty = {NULL, 0};
    prefix = byte_views_prefix(empty, 0);
    assert(prefix.data == NULL && prefix.len == 0);
    assert(extra_aliases_read() == 26);
    assert(extra_return_borrow() == 29);
    assert(extra_wrap_product() == 1);
    assert(extra_write_return() == 41);
    assert(extra_copied_parameter(view).data == input);
    assert(extra_bool_order(1, 2));
    assert(extra_bool_order(2, 4));
    assert(!extra_bool_order(4, 2));
    assert(!extra_bool_order(2, 3));
    reset();
    assert(owners_allocation_sum(8) == 7);
    assert(allocations == 1 && releases == 1);
    reset();
    assert(owners_allocation_transfer(9) == 9);
    assert(allocations == 1 && releases == 1);
    reset();
    assert(owners_allocation_transfer(0) == 0);
    assert(allocations == 0 && releases == 0);
    reset(); fail_at = 1;
    assert(owners_allocation_sum(8) == 0);
    assert(allocations == 0 && releases == 0);
    reset();
    assert(extra_allocation_first(8) == 91);
    assert(allocations == 1 && releases == 1);
    reset();
    assert(extra_nested() == 8);
    assert(allocations == 2 && releases == 2 && freed[0] == 5 && freed[1] == 3);
    reset(); fail_at = 2;
    assert(extra_nested() == 3);
    assert(allocations == 1 && releases == 1 && freed[0] == 3);
    reset();
    assert(extra_loop_cleanup(5) == 2);
    assert(allocations == 2 && releases == 2);
    reset();
    assert(extra_loop_continue(4) == 4);
    assert(allocations == 4 && releases == 4);
    reset();
    assert(extra_zeroed() == 0);
    assert(allocations == 1 && releases == 1);
    return 0;
}
"""


def refusal_controls(work):
    fixtures = [
        ("exclusive_alias", "(array bytes 8) (borrow one write bytes) (borrow two write bytes) (return)"),
        ("read_then_write", "(array bytes 8) (borrow one read bytes) (put bytes (usize 0) (u8 1)) (return)"),
        ("owner_read_frozen", "(array bytes 8) (borrow one write bytes) (let x usize (len bytes)) (return)"),
        ("drop_borrowed", "(array bytes 8) (borrow one read bytes) (drop bytes) (return)"),
        ("move_borrowed", "(array bytes 8) (borrow one read bytes) (move other bytes) (return)"),
        ("use_after_move", "(array bytes 8) (move other bytes) (put bytes (usize 0) (u8 1)) (return)"),
        ("use_after_end", "(array bytes 8) (borrow one read bytes) (end one) (let x usize (len one)) (return)"),
        ("use_after_drop", "(array bytes 8) (drop bytes) (let x usize (len bytes)) (return)"),
        ("copy_owner", "(array bytes 8) (copy other bytes) (return)"),
        ("copy_unique", "(array bytes 8) (borrow one write bytes) (copy other one) (return)"),
        ("end_parent", "(array bytes 8) (borrow one write bytes) (borrow child read one) (end one) (return)"),
        ("write_reborrowed", "(array bytes 8) (borrow one write bytes) (borrow child read one) (put one (usize 0) (u8 1)) (return)"),
        ("readonly_write", "(array bytes 8) (borrow one read bytes) (put one (usize 0) (u8 1)) (return)"),
        ("branch_owner", "(array bytes 8) (if true (then (drop bytes)) (else)) (return)"),
        ("loop_owner", "(array bytes 8) (while true (body (drop bytes))) (return)"),
        ("break_owner", "(array bytes 8) (while true (body (drop bytes) (break))) (return)"),
        ("uninitialised", "(let value u8 unknown) (return)"),
        ("mutable_only", "(let value u8 (u8 1)) (set value (u8 2)) (return)"),
        ("implicit_narrow", "(let value u8 (as u8 (u64 256))) (return)"),
        ("wrong_store_type", "(array bytes 8) (put bytes (usize 0) (u64 1)) (return)"),
        ("wrong_index_type", "(array bytes 8) (put bytes (u8 0) (u8 1)) (return)"),
        ("missing_effect", "(allocate bytes (usize 1) (then (return)) (else (return)))"),
        ("break_outside", "(break)"),
    ]
    cases = [(name, module(body)) for name, body in fixtures]
    cases += [
        ("escape_array", module("(array bytes 8) (borrow view read bytes) (return view)",
                               result="read from input", params="(input read)")),
        ("wrong_origin", module("(return other)", result="read from input",
                               params="(input read) (other read)")),
        ("owned_cleanup_effect", module("(return)", params="(input own)")),
        ("call_unique_alias", module("(array bytes 8) (call target bytes bytes) (return)",
             declarations="(fn target (params (a write) (b read)) (returns unit) (effects) (body (return)))")),
        ("call_owned_alias", module("(call target input input) (return)", params="(input own)", effects="heap",
             declarations="(fn target (params (a own) (b own)) (returns unit) (effects heap) (body (return)))")),
        ("foreign_effect", module("(call print) (return)", declarations=
             '(foreign print native_print (params) (returns unit) (effects io) (obligation "I/O"))')),
        ("ignored_owner", module("(call maker input) (return)", params="(input own)", effects="heap",
             declarations="(fn maker (params (bytes own)) (returns own) (effects) (body (return bytes)))")),
        ("missing_return", module("(let value u8 (u8 1))", result="u8")),
        ("unreachable", module("(return) (array bytes 1)")),
        ("wrong_from", module("(return)", declarations=
             "(foreign bad native_bad (params (bytes read)) (returns write from bytes) (effects) (obligation \"bad\"))")),
    ]
    for name, text in cases:
        path = work / (name + ".cesta")
        path.write_text(text)
        result = run([BINARY, "checked", "check", path], success=False)
        assert b"error:" in result.stderr and b"| " in result.stderr, result.stderr
    return len(cases)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sanitizers", action="store_true")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="checked-core-", dir=ROOT / "build"))
    sources = [ROOT / "checked/examples/ByteViews.cesta", ROOT / "checked/examples/Owners.cesta",
               work / "Extra.cesta"]
    sources[-1].write_text(MORE)
    for name, source in zip(("ByteViews", "Owners", "Extra"), sources):
        run([BINARY, "checked", "check", source])
        run([BINARY, "checked", "header", source, "-o", work / (name + ".h")])
        run([BINARY, "checked", "plan", source, "-o", work / (name + ".plan")])
        run([BINARY, "export-c", source, "-o", work / (name + ".c"), "--"])
        again = run([BINARY, "export-c", source, "--"]).stdout
        assert again == (work / (name + ".c")).read_bytes(), "export changed between identical runs"
        assert b"#line" not in again
    refusals = refusal_controls(work)
    (work / "runtime.c").write_text(RUNTIME)
    builds = []
    for compiler in ("gcc", "clang"):
        for optimization in ("-O0", "-O2"):
            executable = work / (compiler + optimization)
            flags = ["-std=c11", optimization, "-g0", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-I", work]
            run([compiler, *flags, *[work / (n + ".c") for n in ("ByteViews", "Owners", "Extra")],
                 work / "runtime.c", "-o", executable])
            run([executable], cwd=work)
            for fault in ("bounds", "overflow", "shift", "division", "require"):
                failed = run([executable, fault], cwd=work, success=False)
                assert failed.returncode == 73, (compiler, optimization, fault, failed.returncode)
            builds.append({"compiler": compiler, "optimization": optimization, "native": True})
        hosted = work / (compiler + "-authored")
        run([BINARY, "cc", "--cc", compiler, "-std=c11", "-O2", "-Wall", "-Wextra",
             "-Wpedantic", "-Werror", "-I", work, *sources, work / "runtime.c", "-o", hosted])
        run([hosted], cwd=work)
        print(compiler + ": checked views, moves, borrowed returns, owner cleanup, allocation failure and traps passed", flush=True)
    if args.sanitizers:
        executable = work / "sanitized"
        run(["clang", "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
             "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-I", work,
             *[work / (n + ".c") for n in ("ByteViews", "Owners", "Extra")],
             work / "runtime.c", "-o", executable])
        run([executable], cwd=work)
        for fault in ("bounds", "overflow", "shift", "division", "require"):
            failed = run([executable, fault], cwd=work, success=False)
            assert failed.returncode == 73, (fault, failed.returncode, failed.stderr)
        print("clang ASan/UBSan: ownership, cleanup, reborrowing, empty views and traps passed", flush=True)
    summary = {"binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
               "refusals": refusals, "builds": builds, "sanitizers": args.sanitizers, "work": str(work)}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "build/checked-core-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("checked core:", refusals, "refusals;", len(builds), "native builds; authored GCC/Clang builds")


if __name__ == "__main__":
    main()
