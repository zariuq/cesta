"""Independent byte packing and formatting controls for checked functions."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from checked_core import BINARY, ROOT, run

CONTROL = r'''
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "PackedWords.h"
#include "FormatBytes.h"

void packed_words_failure(void) { _Exit(78); }
void format_bytes_failure(void) { _Exit(78); }

static uint64_t random_word(void)
{
    static uint64_t state = UINT64_C(0x97a4521b3c69d8ef);
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
}

int main(int argc, char **argv)
{
    uint8_t bytes[32] = {0}, actual[64] = {0};
    char expected[64];
    if (argc > 1) {
        if (!strcmp(argv[1], "packing")) {
            cesta_read_bytes input = {bytes, 9};
            return (int)packed_words_load_le64(input);
        }
        if (!strcmp(argv[1], "format")) {
            cesta_write_bytes output = {NULL, 0};
            return (int)format_bytes_unsigned_decimal(output, 0);
        }
        if (!strcmp(argv[1], "metadata"))
            return (int)packed_words_display_metadata(256, 1, 0);
        return 2;
    }
    for (size_t length = 0; length <= 8; ++length) {
        for (size_t trial = 0; trial < 2048; ++trial) {
            uint64_t word = random_word();
            memset(bytes, 0xa5, sizeof bytes);
            cesta_write_bytes output = {bytes, length};
            cesta_read_bytes input = {bytes, length};
            packed_words_store_le64(output, word);
            for (size_t i = 0; i < length; ++i)
                assert(bytes[i] == (uint8_t)(word >> (8 * i)));
            for (size_t i = length; i < sizeof bytes; ++i)
                assert(bytes[i] == 0xa5);
            uint64_t mask = length == 8 ? UINT64_MAX : (UINT64_C(1) << (8 * length)) - 1;
            assert(packed_words_load_le64(input) == (word & mask));
        }
    }
    for (size_t i = 0; i < 8192; ++i) {
        uint64_t word = i < 2 ? (i ? UINT64_MAX : 0) : random_word();
        cesta_write_bytes output = {actual, sizeof actual};
        memset(actual, 0xa5, sizeof actual);
        size_t length = format_bytes_unsigned_decimal(output, word);
        int n = snprintf(expected, sizeof expected, "%" PRIu64, word);
        assert(n >= 0 && length == (size_t)n && !memcmp(actual, expected, length));
        assert(actual[length] == 0xa5);
        for (int prefix = 0; prefix <= 1; ++prefix) {
            memset(actual, 0xa5, sizeof actual);
            length = format_bytes_unsigned_hex(output, word, prefix != 0);
            n = snprintf(expected, sizeof expected, prefix ? "0x%" PRIx64 : "%" PRIx64, word);
            assert(n >= 0 && length == (size_t)n && !memcmp(actual, expected, length));
            assert(actual[length] == 0xa5);
        }
        size_t offset = (size_t)(word & 255);
        size_t count = (size_t)((word >> 8) % 9);
        uint32_t color = (uint32_t)(word >> 32);
        assert(packed_words_display_metadata(offset, count, color) ==
            ((uint64_t)offset | ((uint64_t)count << 8) | ((uint64_t)color << 32)));
    }
    memset(bytes, 0x6b, sizeof bytes);
    cesta_write_bytes output = {actual, sizeof actual};
    cesta_read_bytes input = {bytes, sizeof bytes};
    assert(format_bytes_copy(output, input) == sizeof bytes);
    assert(!memcmp(actual, bytes, sizeof bytes));
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sanitizers", action="store_true")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="checked-libraries-", dir=ROOT / "build"))
    sources = [ROOT / "checked/examples" / (name + ".cesta") for name in ("PackedWords", "FormatBytes")]
    for source in sources:
        run([BINARY, "checked", "header", source, "-o", work / (source.stem + ".h")])
        run([BINARY, "export-c", source, "-o", work / (source.stem + ".c"), "--"])
    harness = work / "control.c"
    harness.write_text(CONTROL)
    for compiler in ("gcc", "clang"):
        for optimization in ("-O0", "-O2"):
            executable = work / (compiler + optimization)
            run([compiler, "-std=c11", optimization, "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                 "-I", work, *[work / (source.stem + ".c") for source in sources], harness, "-o", executable])
            run([executable], cwd=work)
            for fault in ("packing", "format", "metadata"):
                result = run([executable, fault], cwd=work, success=False)
                assert result.returncode == 78, (compiler, optimization, fault, result.returncode)
        executable = work / (compiler + "-authored")
        run([BINARY, "cc", "--cc", compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Wpedantic",
             "-Werror", "-I", work, *sources, harness, "-o", executable])
        run([executable], cwd=work)
        print(compiler + ": 18,432 packing cases; 24,576 numeric formats; metadata, guards and authored builds passed", flush=True)
    if args.sanitizers:
        executable = work / "sanitized"
        run(["clang", "-std=c11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
             "-I", work, *[work / (source.stem + ".c") for source in sources], harness, "-o", executable])
        run([executable], cwd=work)
        print("packing and formatting ASan/UBSan passed", flush=True)
    summary = {"binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(), "work": str(work),
               "packing_cases": 18432, "format_cases": 24576, "sanitizers": args.sanitizers}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "build/checked-libraries-summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
