"""Source trees: every byte of a C source file, read through translation
phases 1-3 and the directive structure of 6.10, printed back exactly.

Each core case is preprocessed by GCC and Clang in the same dialect first:
the cases Cesta reads are the ones both compilers accept, and the cases it
refuses are the ones both refuse. The corpora's own sources and headers are
then read in both dialects.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from corpus import CETTA_CHECKOUT

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests/source"
MODES = ("gnu11", "c11")

# Constructors each core case must contain in each dialect, beyond exactness.
STRUCTURE = {
    "comments.c": {"gnu11": ["DefineObject"], "c11": ["DefineObject"]},
    "trigraphs.c": {"c11": ["DefineObject"], "gnu11": ["TextLine"]},
    "digraphs.c": {"gnu11": ["DefineObject", "IfSection", "EndifLine"]},
    "raw-strings.c": {"gnu11": ["PPRawString"], "c11": ["PPUnterminated"]},
    "quotes.c": {"gnu11": ["PPUnterminated", "PPCharacter"]},
    "header-names.c": {"gnu11": ["PPHeaderName", "Include"]},
    "lenient-directives.c": {"gnu11": ["LenientDefine", "LenientIfDefined", "LenientEndif",
                                       "LenientInclude", "LenientUndef", "LenientElse"]},
    "gnu-directives.c": {"gnu11": ["Ident", "Sccs", "Warning", "ElifDefined", "ElifNotDefined",
                                   "LineMarker", "NullDirective", "NonDirectivePart", "Assert",
                                   "Import", "IncludeNext", "Embed"]},
    "defines.c": {"gnu11": ["DefineObject", "DefineFunction", "DefineVariadic", "DefineVariadicList",
                            "DefineNamedVariadic", "NoIdentifiers", "Undef"]},
    "standard-examples.c": {"gnu11": ["DefineFunction", "Undef", "TextLine"]},
}

REFUSALS = {
    "unterminated-comment.c": "unterminated comment",
    "endif-without-if.c": "#endif without #if",
    "unterminated-if.c": "unterminated #if",
    "else-after-else.c": "#else after #else",
    "elif-after-else.c": "#elif after #else",
    "unterminated-raw-string.c": "unterminated raw string literal",
}


def run(arguments, **options):
    return subprocess.run([str(x) for x in arguments], capture_output=True, **options)


def compilers_accept(path, mode):
    return [run([cc, "-std="+mode, "-E", "-P", path]).returncode == 0 for cc in ("gcc", "clang")]


def corpus_files():
    sources = [CETTA_CHECKOUT / "src", CETTA_CHECKOUT / "runtime",
               ROOT.parent / "repos/swipl-devel/src"]
    files = sorted(p for base in sources for p in base.rglob("*") if p.suffix in (".c", ".h") and p.is_file())
    if not files:
        raise SystemExit("source trees: the corpora's sources are not present")
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta", type=Path, default=ROOT / "build/cesta")
    parser.add_argument("--core-only", action="store_true", help="the core cases and refusals, without the corpora")
    options = parser.parse_args()
    cesta = options.cesta.resolve()
    failures = []
    # A source document is one versioned root. Prefix-matched versions and
    # strings outside that root must not be concatenated into source bytes.
    malformed = [b'(cesta source/10 "int x;")', b'(cesta source/1x "int x;")',
                 b'(cesta source/1 "int x;") "int injected;"',
                 b'(cesta source/1 "int x;") (cesta source/1 "int injected;")',
                 b'(cesta source/1 "int x;"', b'(cesta source/1 "int x;"))']
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        document = Path(temporary) / "source.cesta"
        for data in malformed:
            document.write_bytes(data)
            refused = run([cesta, "source-bytes", document])
            if refused.returncode == 0 or refused.stdout:
                failures.append("source decoder accepted a malformed or multi-root document")
        document.write_bytes(b'(cesta source/1 "int x;") ; trailing document comment\n')
        restored = run([cesta, "source-bytes", document])
        if restored.returncode or restored.stdout != b"int x;":
            failures.append("source decoder refused a valid root with a trailing comment")

    cases = sorted(CASES.glob("*.c"))
    for path in cases:
        for mode in MODES:
            if not all(compilers_accept(path, mode)):
                failures.append(f"{path.name} {mode}: a compiler refuses this core case")
                continue
            checked = run([cesta, "source", "--std="+mode, "--check", path])
            if checked.returncode:
                failures.append(f"{path.name} {mode}: {checked.stderr.decode(errors='replace').strip()}")
                continue
            document = run([cesta, "source", "--std="+mode, path]).stdout.decode(errors="replace")
            for label in STRUCTURE.get(path.name, {}).get(mode, []):
                if "(c:"+label+" " not in document and "(c:"+label+")" not in document:
                    failures.append(f"{path.name} {mode}: expected {label}")

    for name, message in REFUSALS.items():
        path = CASES / "refused" / name
        if any(compilers_accept(path, "gnu11")):
            failures.append(f"{name}: a compiler accepts this refusal case")
        refused = run([cesta, "source", "--std=gnu11", "--check", path])
        if refused.returncode == 0 or message not in refused.stderr.decode(errors="replace"):
            failures.append(f"{name}: expected the refusal '{message}', got {refused.stderr.decode(errors='replace').strip()!r}")

    if options.core_only:
        if failures:
            for failure in failures: print("source trees:", failure, file=sys.stderr)
            raise SystemExit(f"source trees: {len(failures)} failures")
        print(f"source trees: {len(cases)} core cases exact in both dialects and accepted by GCC and Clang; "
              f"{len(REFUSALS)} refusals shared with both compilers; {len(malformed)} malformed documents refused")
        return
    files = corpus_files()
    totals = {}
    for mode in MODES:
        lines = tokens = directives = 0
        for begin in range(0, len(files), 64):
            batch = files[begin:begin+64]
            checked = run([cesta, "source", "--std="+mode, "--check", *batch])
            for row in checked.stdout.decode(errors="replace").splitlines():
                head, _, tail = row.rpartition(": ")
                counts = tail.split(";")[0].split(", ")
                lines += int(counts[0].split()[0]); tokens += int(counts[1].split()[0])
                directives += int(counts[2].split()[0])
            if checked.returncode:
                failures.extend(f"{mode}: {line}" for line in checked.stderr.decode(errors="replace").splitlines())
        totals[mode] = (lines, tokens, directives)

    if failures:
        for failure in failures: print("source trees:", failure, file=sys.stderr)
        raise SystemExit(f"source trees: {len(failures)} failures")
    lines, tokens, directives = totals["gnu11"]
    print(f"source trees: {len(files)} corpus files exact in gnu11 and c11 ({lines} lines, {tokens} tokens, "
          f"{directives} directives); {len(cases)} core cases exact in both dialects and accepted by GCC and Clang; "
          f"{len(REFUSALS)} refusals shared with both compilers")


if __name__ == "__main__":
    main()
