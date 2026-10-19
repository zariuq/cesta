"""Layouts: declared words are read, normalized and checked; every malformed declaration is refused."""

from pathlib import Path
import random
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracles.pro_compact_cesta import parse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
MODULE = ROOT / "tests/layouts/CeTTa.module"
WORK = ROOT / "build/layouts"

ACCEPTED = {"Frames": ["FrameIdentity", "VarId"], "AtomsSplit": ["AtomFlags", "AtomHashValid"],
            "FramesWide": ["FrameIdentity", "VarId", "FrameIdentityWide"]}

# (theory, text replaced once, replacement, part of the expected refusal)
MUTANTS = [
    ("Frames", "(Place generation (Bits 20 11) (Range 1 2046))", "(Place generation (Bits 19 12) (Range 1 2046))",
     "fields handle and generation overlap"),
    ("Frames", "(Bits 20 11) (Range 1 2046)", "(Bits 20 12) (Range 1 2046)", "lies outside the 31 bits"),
    ("Frames", "(Bits 20 11) (Range 1 2046)", "(Bits 20 10) (Range 1 1022)", "bit 30 below the case prefix is in no field"),
    ("Frames", "(Case (Prefix 3 6)", "(Case (Prefix 3 5)", "extends the prefix of an earlier case"),
    ("Frames", "(Case (Prefix 4 14)", "(Case (Prefix 4 15)", "shared with or begins another case's prefix"),
    ("Frames", """        (Reserved (Prefix 4 15)
          (Place handle (Bits 0 28) (Offset HandleLimit))
          (Place generation (Bits 28 0)))
""", "", "top 4 bits are 15; declare it Reserved"),
    ("Frames", "(Range 1 62)", "(Range 1 64)", "does not fit offset 0 and 6 bits"),
    ("Frames", "(Range 1 2046)", "(Range 5 2)", "admits no value"),
    ("Frames", "(Place handle (Bits 0 24) (Offset ClassOneFirst))", "(Place handle (Bits 0 24) (Offset 0))",
     "admit a common value"),
    ("Frames", "(Place handle (Bits 0 24) (Offset ClassOneFirst))",
     "(Place handle (Bits 0 24) (Offset 0) (Range ClassOneFirst (Minus (Pow2 24) 1)))",
     "read some words as the same value"),
    ("Frames", '(Field handle "value")', '(Field handle "number")', "unknown field kind number"),
    ("Frames", '(Field handle "value")', '(Field handle "any-child")', "kind any-child needs a word with a single case"),
    ("Frames", "\n          (Place generation (Bits 26 2) (Range 1 2)))", ")", "case does not place field generation"),
    ("Frames", "(Place handle (Bits 0 26) (Offset ClassThreeFirst))",
     "(Place handle (Bits 0 26) (Offset ClassThreeFirst)) (Place handle (Bits 0 26) (Offset ClassThreeFirst))",
     "places field handle twice"),
    ("Frames", "(Place generation (Bits 26 3) (Range 1 6))", "(Place gen (Bits 26 3) (Range 1 6))",
     "places undeclared field gen"),
    ("Frames", "(Offset ClassTwoFirst)", "(Offset ClassTwo)", "unknown layout constant ClassTwo"),
    ("Frames", "(Const ClassOneFirst (Pow2 20))", "(Const ClassOneFirst (Pow2 64))", "power of two does not fit"),
    ("Frames", "(Prefix 4 14)", "(Prefix 4 16)", "prefix value does not fit"),
    ("Frames", """        (Location "immutable"));
      (VarId)""", """);
      (VarId)""", "requires a (Location DISCIPLINE)"),
    ("Frames", '(Location "immutable"));\n      (VarId)', '(Location "frozen"));\n      (VarId)',
     "unknown write discipline frozen"),
    ("Frames", "(VarId) ==", "(FrameIdentity) ==", "layout declared twice"),
    ("Frames", "(Word 64)", "(Word 65)", "word has 1 to 64 bits"),
    ("AtomsSplit", "(AtMost var_bloom 24)", "(AtMost var_bloom 21)", "wider than its requirement allows"),
    ("FramesWide", "(Range 1 (Minus (Pow2 32) 2))", "(Range 1 1000)",
     "no case of FrameIdentityWide admits every value this case of FrameIdentity admits"),
    ("FramesWide", "(From FrameIdentity)", "(From VarId)", "a newer version reads the same fields, in the same order"),
    ("FramesWide", "(From FrameIdentity)", "(From FrameIdentityLater)", "is not declared before this layout"),
    ("AtomsSplit", "(Alias term_stable hash_stable", "(Alias term_stable hash_stabel", "alias names an undeclared field"),
    ("AtomsSplit", '(Location "atomic")', '(Location "single-owner")',
     "hash_valid is written after publication in a word that is not atomic"),
]


def check(module, theory, success=True):
    result = subprocess.run([str(BINARY), "layout", "check", str(module), theory], capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{theory}: status {result.returncode}\n{result.stderr.decode(errors='replace')}")
    return result


# ---- An independent reading of the normalized layouts -----------------------

def number(a):
    return int(str(a))


class Layout:
    def __init__(self, data):
        self.name, self.width = str(data[1]), number(data[2])
        self.fields = [(str(f[0]), f[1].data.decode()) for f in data[3][1:]]
        self.leaves = []
        self.trie = data[4]
        self.collect(self.trie, 0, 0)
        last = data[len(data) - 1]
        self.origin = str(last[1]) if str(last[0]) == "From" else None

    def collect(self, node, prefix, depth):
        if str(node[0]) == "Node":
            self.collect(node[1], prefix << 1, depth + 1)
            self.collect(node[2], prefix << 1 | 1, depth + 1)
        else:
            reserved = node[1].data.decode() == "reserved"
            places = [tuple(number(x) for x in place[1:]) for place in node[2:]]
            self.leaves.append((reserved, places, prefix, depth))

    def leaf(self, word):
        node, depth = self.trie, 0
        while str(node[0]) == "Node":
            node = node[2] if word >> (self.width - 1 - depth) & 1 else node[1]
            depth += 1
        for leaf in self.leaves:
            if leaf[3] == depth and word >> (self.width - depth) == leaf[2]:
                return leaf
        raise AssertionError("no leaf")

    def parts(self, word):
        reserved, places, prefix, depth = self.leaf(word)
        low = word % (1 << (self.width - depth))
        return [(low >> s) % (1 << n) + off for s, n, off, lo, hi in places]

    @staticmethod
    def admits(leaf, values):
        return not leaf[0] and all(lo <= v <= hi for v, (s, n, off, lo, hi) in zip(values, leaf[1]))

    def valid(self, word):
        return self.admits(self.leaf(word), self.parts(word))

    def encode(self, values):
        for leaf in self.leaves:
            if self.admits(leaf, values):
                reserved, places, prefix, depth = leaf
                word = prefix << (self.width - depth)
                for v, (s, n, off, lo, hi) in zip(values, places):
                    word |= ((v - off) % (1 << n)) << s
                return word
        return None

    def masks(self):
        places = self.leaves[0][1]
        kinds = {}
        for (name, kind), (s, n, off, lo, hi) in zip(self.fields, places):
            kinds[kind] = kinds.get(kind, 0) | (((1 << n) - 1) << s)
        return kinds


def c_array(name, values):
    return f"static const uint64_t {name}[] = {{{', '.join(f'UINT64_C({v})' for v in values) or '0'}}};\n"


def harness(layouts, header):
    """A C program checking the generated accessors against the reading above."""
    rng = random.Random(20261019)
    body, checks = [f'#include "{header}"', "#include <stdio.h>", "#include <stdlib.h>",
                    "static int failures;",
                    "static void check(const char *what, uint64_t got, uint64_t expected) {",
                    '    if (got != expected) { fprintf(stderr, "%s: %llu, expected %llu\\n", what, '
                    "(unsigned long long)got, (unsigned long long)expected); ++failures; }", "}"], []
    for layout in layouts:
        n, w, top = layout.name, layout.width, (1 << layout.width) - 1
        words = {0, top} | {rng.randrange(1 << w) for _ in range(3000)}
        for reserved, places, prefix, depth in layout.leaves:
            base = prefix << (w - depth)
            words |= {base, base | ((1 << (w - depth)) - 1)}
        words = sorted(words)
        body.append(c_array(f"{n}_words", words))
        for j, (field, kind) in enumerate(layout.fields):
            body.append(c_array(f"{n}_{field}_parts", [layout.parts(x)[j] for x in words]))
            checks.append(f'for (size_t i = 0; i < sizeof {n}_words / sizeof *{n}_words; ++i) '
                          f'check("{n}_{field}", {n}_{field}({n}_words[i]), {n}_{field}_parts[i]);')
        body.append(c_array(f"{n}_valid_expected", [int(layout.valid(x)) for x in words]))
        checks.append(f'for (size_t i = 0; i < sizeof {n}_words / sizeof *{n}_words; ++i) '
                      f'check("{n}_valid", {n}_valid({n}_words[i]), {n}_valid_expected[i]);')
        values = []
        for reserved, places, prefix, depth in layout.leaves:
            for _ in range(200):
                values.append([rng.choice([lo, hi, lo + 1, max(lo, hi - 1), rng.randint(lo, hi) if lo <= hi else lo,
                                           hi + 1, max(lo, 1) - 1, off + (1 << n), 0])
                               for s, n, off, lo, hi in places])
        values = [[v % (1 << 64) for v in vs] for vs in values]
        arity = len(layout.fields)
        flat = [v for vs in values for v in vs]
        body.append(c_array(f"{n}_values", flat))
        body.append(c_array(f"{n}_admits_expected", [int(layout.encode(vs) is not None) for vs in values]))
        body.append(c_array(f"{n}_encode_expected", [layout.encode(vs) or 0 for vs in values]))
        arguments = ", ".join(f"{n}_values[i * {arity} + {j}]" for j in range(arity))
        checks.append(f'for (size_t i = 0; i < sizeof {n}_admits_expected / sizeof *{n}_admits_expected; ++i) {{ '
                      f'check("{n}_admits", {n}_admits({arguments}), {n}_admits_expected[i]); '
                      f'check("{n}_encode", {n}_encode({arguments}), {n}_encode_expected[i]); }}')
        if len(layout.leaves) == 1:
            places = layout.leaves[0][1]
            for j, (field, kind) in enumerate(layout.fields):
                s, nb, off, lo, hi = places[j]
                writes = [(x, rng.randint(lo, hi) if lo <= hi else lo) for x in words[:500]]
                expected = [(x & ~(((1 << nb) - 1) << s) | (((v - off) % (1 << nb)) << s)) & top for x, v in writes]
                body.append(c_array(f"{n}_set_{field}_words", [x for x, v in writes]))
                body.append(c_array(f"{n}_set_{field}_values", [v for x, v in writes]))
                body.append(c_array(f"{n}_set_{field}_expected", expected))
                checks.append(f'for (size_t i = 0; i < sizeof {n}_set_{field}_words / sizeof *{n}_set_{field}_words; '
                              f'++i) check("{n}_set_{field}", {n}_set_{field}({n}_set_{field}_words[i], '
                              f'{n}_set_{field}_values[i]), {n}_set_{field}_expected[i]);')
            masks = layout.masks()
            if any(k in masks for k in ("any-child", "all-children", "cache", "occurrence")):
                anym, allm = masks.get("any-child", 0), masks.get("all-children", 0)
                ownm = masks.get("value", 0) | masks.get("fact", 0) | masks.get("reference", 0)
                every = sum(masks.values())
                kept = every & ~masks.get("occurrence", 0)
                for k in range(300):
                    children = [rng.randrange(1 << w) for _ in range(rng.randint(0, 5))]
                    own = rng.randrange(1 << w)
                    orall = 0
                    andall = allm
                    for c in children:
                        orall |= c
                        andall &= c
                    expected = (orall & anym) | (andall & allm) | (own & ownm)
                    if children:
                        body.append(c_array(f"{n}_children{k}", children))
                    steps = "".join(f"acc = {n}_derive_child(acc, {n}_children{k}[{i}]); " for i in range(len(children)))
                    checks.append(f'{{ {"uint" + str(w) + "_t"} acc = {n}_derive_init(); {steps}'
                                  f'check("{n}_derive", {n}_derive_finish(acc, UINT64_C({own})), UINT64_C({expected})); }}')
                for x in words[:500]:
                    checks.append(f'check("{n}_store", {n}_store(UINT64_C({x})), UINT64_C({x & kept})); '
                                  f'check("{n}_value_copy", {n}_value_copy(UINT64_C({x})), UINT64_C({x & every})); '
                                  f'check("{n}_relocate", {n}_relocate(UINT64_C({x})), UINT64_C({x & every}));')
    named = {layout.name: layout for layout in layouts}
    for layout in layouts:
        if layout.origin:
            old, n = named[layout.origin], f"{layout.name}_from_{layout.origin}"
            words = sorted({rng.randrange(1 << old.width) for _ in range(5000)} |
                           {leaf[2] << (old.width - leaf[3]) | ((1 << (old.width - leaf[3])) - 1) for leaf in old.leaves})
            expected = [layout.encode(old.parts(x)) if old.valid(x) else 0 for x in words]
            body.append(c_array(f"{n}_words", words))
            body.append(c_array(f"{n}_expected", expected))
            checks.append(f'for (size_t i = 0; i < sizeof {n}_words / sizeof *{n}_words; ++i) '
                          f'check("{n}", {n}({n}_words[i]), {n}_expected[i]);')
            # Every migrated word reads back as the old word's value.
            checks.append(f'for (size_t i = 0; i < sizeof {n}_words / sizeof *{n}_words; ++i) '
                          f'if ({old.name}_valid({n}_words[i])) {{ check("{n} handle", {layout.name}_{layout.fields[0][0]}('
                          f'{n}({n}_words[i])), {old.name}_{old.fields[0][0]}({n}_words[i])); }}')
    body.append("int main(void) {")
    body += ["    " + c for c in checks]
    body.append('    if (failures) { fprintf(stderr, "%d failures\\n", failures); return 1; }')
    body.append("    return 0;\n}")
    return "\n".join(body) + "\n"


def native(theory, layouts):
    header = WORK / f"{theory}.h"
    check(MODULE, theory)
    subprocess.run([str(BINARY), "layout", "header", str(MODULE), theory, "-o", str(header)], check=True)
    again = WORK / f"{theory}.again.h"
    subprocess.run([str(BINARY), "layout", "header", str(MODULE), theory, "-o", str(again)], check=True)
    assert header.read_bytes() == again.read_bytes(), theory + ": header differs between runs"
    source = WORK / f"{theory}_run.c"
    source.write_text(harness(layouts, header.name))
    runs = 0
    for cc in ("gcc", "clang"):
        for std in ("c11", "gnu11"):
            for opt in ("-O0", "-O2"):
                exe = WORK / f"{theory}_{cc}_{std}{opt}"
                build = subprocess.run([cc, f"-std={std}", opt, "-Wall", "-Wextra", "-Werror", "-pedantic",
                                        "-Wno-overlength-strings", str(source), "-o", str(exe)], capture_output=True)
                if build.returncode:
                    raise AssertionError(build.stderr.decode(errors="replace")[-4000:])
                result = subprocess.run([str(exe)], capture_output=True)
                if result.returncode:
                    raise AssertionError(f"{theory} {cc} {std} {opt}: " + result.stderr.decode(errors="replace")[-4000:])
                runs += 1
    return runs


CONCURRENT = ROOT / "tests/layouts/Concurrent.module"
LANES = r'''#include "lanes.h"
#include <pthread.h>
#include <stdio.h>

#ifdef PLAIN
static uint32_t word;
#define SET(f, v) (word = Lanes_set_##f(word, v))
#define LOAD() (word)
#else
static _Atomic uint32_t word;
#define SET(f, v) Lanes_atomic_set_##f(&word, v)
#define LOAD() Lanes_atomic_load(&word)
#endif

#define LANE(f, last) static void *lane_##f(void *arg) { (void)arg; for (unsigned i = 0; i < 20000; ++i) { SET(f, i % 200); } SET(f, last); return NULL; }
LANE(a, 201) LANE(b, 202) LANE(c, 203) LANE(d, 204)

int main(void) {
    pthread_t t[4];
    pthread_create(&t[0], NULL, lane_a, NULL); pthread_create(&t[1], NULL, lane_b, NULL);
    pthread_create(&t[2], NULL, lane_c, NULL); pthread_create(&t[3], NULL, lane_d, NULL);
    unsigned long seen = 0;
    for (int i = 0; i < 20000; ++i) seen += Lanes_a(LOAD());
    for (int i = 0; i < 4; ++i) pthread_join(t[i], NULL);
    uint32_t w = LOAD();
    if (Lanes_a(w) != 201 || Lanes_b(w) != 202 || Lanes_c(w) != 203 || Lanes_d(w) != 204) {
        fprintf(stderr, "lost update: %08x (%lu)\n", (unsigned)w, seen); return 1;
    }
    return 0;
}
'''


def concurrency():
    """Four threads write their own field of one atomic word: no race, no lost update; the same
    writes on a plain word are a data race."""
    subprocess.run([str(BINARY), "layout", "header", str(CONCURRENT), "Counters", "-o", str(WORK / "lanes.h")],
                   check=True)
    (WORK / "lanes.c").write_text(LANES)
    for cc in ("gcc", "clang"):
        for plain in (False, True):
            exe = WORK / f"lanes-{cc}{'-plain' if plain else ''}"
            subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-fsanitize=thread", "-Wall", "-Wextra", "-Werror",
                            *(["-DPLAIN"] if plain else []), str(WORK / "lanes.c"), "-o", str(exe), "-lpthread"],
                           check=True)
            result = subprocess.run([str(exe)], capture_output=True)
            race = b"data race" in result.stderr
            if plain:
                assert race, f"{cc}: the plain word's race went unreported"
            else:
                assert result.returncode == 0 and not race, f"{cc}: " + result.stderr.decode(errors="replace")[-3000:]


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    source = MODULE.read_text()
    for theory, names in ACCEPTED.items():
        first, again = check(MODULE, theory), check(MODULE, theory)
        assert first.stdout == again.stdout, theory + ": normalized output differs between runs"
        printed = first.stdout.decode().splitlines()
        assert [line.split()[1] for line in printed] == names, (theory, printed)
        (WORK / f"{theory}.layouts").write_bytes(first.stdout)
    runs = 0
    for theory in ACCEPTED:
        layouts = [Layout(d) for d in parse((WORK / f"{theory}.layouts").read_bytes())]
        runs += native(theory, layouts)
    concurrency()
    refused = check(MODULE, "Atoms", success=False).stderr.decode()
    assert "hash_valid is written after publication in a word that is not atomic" in refused, refused
    for index, (theory, old, new, expected) in enumerate(MUTANTS):
        # Mutate the named theory's own block only.
        start = source.index(f"Theory {theory}()")
        end = source.find("\n  Theory ", start)
        end = len(source) if end < 0 else end
        block = source[start:end]
        assert old in block, f"mutant {index}: text not found in {theory}"
        mutant = WORK / f"mutant{index}.module"
        mutant.write_text(source[:start] + block.replace(old, new, 1) + source[end:])
        message = check(mutant, theory, success=False).stderr.decode()
        assert expected in message, f"mutant {index}: expected {expected!r}, got {message!r}"
    print(f"layouts: {sum(map(len, ACCEPTED.values()))} declared layouts read and normalized; generated accessors "
          f"agree with an independent reading on boundary and random words in {runs} native builds; four threads "
          "writing one atomic word through its accessors neither race nor lose updates under ThreadSanitizer, "
          "while the same writes on a plain word are reported as a data race; "
          f"CeTTa's current flags word refused for its unsynchronized cache bit; {len(MUTANTS)} malformed "
          "declarations refused with their reasons")


if __name__ == "__main__":
    main()
