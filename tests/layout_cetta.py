"""CeTTa's own codecs against the accessors generated from the declared layouts.

At a pinned CeTTa revision, the frame-identity decoder in its header and the
encoder in its source are compiled beside the accessors Cesta generates for
the declared FrameIdentity layout, and compared on every 32-bit word and every
valid identity. VarId's readers are compared on boundary and random words."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
MODULE = ROOT / "tests/layouts/CeTTa.module"
WORK = ROOT / "build/layout-cetta"
CETTA = ROOT.parent / "hyperon/cetta-shared-opt-audit-20260807"
REVISION = "503a6988"

PROBE = r'''#include "frame_identity.h"
#include "frames.h"
#include <inttypes.h>
#include <stdio.h>

typedef uint64_t VarId;
@SPELL@
@VARID@

static int failures;
static void fail(const char *what, uint64_t a, uint64_t b) {
    if (failures++ < 20) fprintf(stderr, "%s: %" PRIu64 " (%" PRIu64 ")\n", what, a, b);
}

int main(void) {
    /* Every word: the total reading and validity. */
    for (uint64_t w = 0; w <= UINT32_MAX; ++w) {
        CettaFrameIdentityParts parts;
        bool valid = cetta_frame_identity_parts((CettaFrameIdentity)w, &parts);
        if (parts.handle != FrameIdentity_handle((uint32_t)w)) fail("handle", w, parts.handle);
        if (parts.generation != FrameIdentity_generation((uint32_t)w)) fail("generation", w, parts.generation);
        if (valid != FrameIdentity_valid((uint32_t)w)) fail("valid", w, valid);
    }
    /* Every valid identity: CeTTa's spelling is the generated encoding. */
    for (uint32_t h = 1; h < CETTA_FRAME_HANDLE_LIMIT; ++h) {
        uint32_t max = frame_identity_generation_max(h);
        if (FrameIdentity_admits(h, 0) || FrameIdentity_admits(h, max + 1)) fail("admits beyond", h, max);
        for (uint32_t g = 1; g <= max; ++g)
            if (frame_identity_spell(h, g) != FrameIdentity_encode(h, g)) fail("encode", h, g);
    }
    if (FrameIdentity_admits(0, 1) || FrameIdentity_admits(CETTA_FRAME_HANDLE_LIMIT, 1)) fail("admits handle", 0, 0);
    /* VarId's halves. */
    uint64_t x = 0x9E3779B97F4A7C15u;
    for (int i = 0; i < 1000000; ++i) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        if (var_base_id(x) != VarId_base(x)) fail("var base", x, var_base_id(x));
        if (var_epoch_suffix(x) != VarId_epoch(x)) fail("var epoch", x, var_epoch_suffix(x));
        if (VarId_encode(var_base_id(x), var_epoch_suffix(x)) != x) fail("var encode", x, x);
    }
    if (failures) { fprintf(stderr, "%d mismatches\n", failures); return 1; }
    return 0;
}
'''


def show(path, revision):
    return subprocess.run(["git", "-C", str(CETTA), "show", f"{revision}:{path}"], capture_output=True,
                          check=True).stdout.decode()


def function(source, signature):
    """The text of the function whose definition starts with `signature`, braces matched."""
    start = source.index(signature)
    depth, i = 0, source.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
        if depth == 0:
            return source[start:i]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default=REVISION)
    args = parser.parse_args()
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "frame_identity.h").write_text(show("src/binding/frame_identity.h", args.revision))
    implementation = show("src/binding/frame_identity.c", args.revision)
    spell = "\n".join(function(implementation, s) for s in
                      ("static uint32_t frame_identity_generation_max(uint32_t handle)",
                       "static CettaFrameIdentity frame_identity_spell("))
    atom = show("src/atom.h", args.revision)
    readers = "\n".join("static " + function(atom, s) for s in
                        ("inline uint32_t var_base_id(VarId id)", "inline uint32_t var_epoch_suffix(VarId id)"))
    (WORK / "probe.c").write_text(PROBE.replace("@SPELL@", spell).replace("@VARID@", readers))
    subprocess.run([str(BINARY), "layout", "header", str(MODULE), "Frames", "-o", str(WORK / "frames.h")], check=True)
    for cc in ("gcc", "clang"):
        exe = WORK / f"probe-{cc}"
        subprocess.run([cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", str(WORK / "probe.c"), "-o", str(exe)],
                       check=True)
        result = subprocess.run([str(exe)], capture_output=True)
        if result.returncode:
            raise AssertionError(f"{cc}: " + result.stderr.decode(errors="replace"))
    print(f"layout CeTTa: at CeTTa {args.revision}, its frame-identity decoder equals the generated reading and "
          "validity on every 32-bit word, its spelling equals the generated encoding for every valid identity, "
          "and its VarId readers equal the generated ones (gcc and clang)")


if __name__ == "__main__":
    main()
