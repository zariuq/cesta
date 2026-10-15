"""Original locations, macro chains and lossless JSON analysis records."""

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, accepted=True):
    result = subprocess.run([str(BINARY), *map(str, arguments)], cwd=ROOT, capture_output=True)
    if (result.returncode == 0) != accepted:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result


def metta(atom):
    if atom["kind"] == "list":
        return b"(" + b" ".join(metta(x) for x in atom["items"]) + b")"
    data = bytes.fromhex(atom["bytes"]) if "bytes" in atom else atom["value"].encode()
    if atom["kind"] == "symbol":
        return data
    out = bytearray(b'"')
    for byte in data:
        if byte in (34, 92): out.extend(b"\\" + bytes([byte]))
        elif byte < 32 or byte >= 127: out.extend(f"\\x{byte:02x}".encode())
        else: out.append(byte)
    return bytes(out) + b'"'


def main():
    count = 0
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
        work = Path(directory)
        header = work / "definitions.h"
        header.write_text("#define INNER(v) ((v) + 1UL)\n#define OUTER(v) INNER(v)\n")
        source = work / "source.c"
        source.write_text('#include "definitions.h"\nunsigned char f(unsigned long x) { return OUTER(x); }\n')
        for compiler in ("gcc", "clang"):
            flags = ["--cc", compiler, "-std=gnu11", source]
            expanded = work / "expanded.i"
            expanded.write_bytes(run(["pp", *flags[:-1], "-P", flags[-1]]).stdout)
            if run(["read", *flags]).stdout != run(["read", expanded]).stdout:
                raise RuntimeError("source read differs from reading the same expanded C")
            count += 1
            for command, head in (("bind", "bindings"), ("types", "types"), ("census", "census")):
                original = run([command, *flags]).stdout
                rows = [json.loads(line) for line in run([command, "--json-lines", *flags]).stdout.splitlines()]
                if not rows or any(row["command"] != command for row in rows):
                    raise RuntimeError("JSON records lost the analysis command")
                reconstructed = b"(" + head.encode() + b" " + b" ".join(metta(row["record"]) for row in rows) + b")"
                original_path, restored_path = work / "original.metta", work / "restored.metta"
                original_path.write_bytes(original); restored_path.write_bytes(reconstructed)
                if run(["pack", original_path]).stdout != run(["pack", restored_path]).stdout:
                    raise RuntimeError("JSON records changed atom kinds or bytes")
                count += 1
        header.write_text("#define INNER(v) ((v) + missing_name)\n#define OUTER(v) INNER(v)\n")
        for command in ("bind", "types", "census", "probe", "explicit"):
            result = run([command, "--cc", "gcc", "-std=gnu11", source], accepted=False)
            diagnostic = result.stderr.decode()
            if not diagnostic.startswith(str(header)+":1:") or "in expansion of INNER" not in diagnostic or "in expansion of OUTER" not in diagnostic:
                raise RuntimeError("refusal lost its original location or expansion chain: " + diagnostic)
            count += 1
        run(["read", "--cc", "gcc", "-std=gnu11", source])
        header.write_text("#define INNER(v) ((v) + )\n#define OUTER(v) INNER(v)\n")
        refusal = run(["read", "--cc", "gcc", "-std=gnu11", source], accepted=False)
        if not refusal.stderr.decode().startswith(str(header)+":1:"):
            raise RuntimeError("syntax refusal lost the macro definition position")
        count += 2
        header.write_text("#define OPEN_BODY { return 1;\n")
        source.write_text('#include "definitions.h"\n\n\nint unfinished(void) OPEN_BODY\n')
        for command in ("read", "bind", "types"):
            refusal = run([command, "--cc", "gcc", "-std=gnu11", source], accepted=False)
            diagnostic = refusal.stderr.decode()
            if (not diagnostic.startswith(str(header)+":1:") or "unexpected EOF" not in diagnostic or
                    "in expansion of OPEN_BODY" not in diagnostic or "expected" not in diagnostic):
                raise RuntimeError("EOF refusal lost its last-token origin or expansion: " + diagnostic)
            count += 1
        # POSIX file names can contain bytes outside UTF-8. JSON retains those
        # as bytes instead of replacing them or emitting invalid JSON.
        unusual = work / "source-\udcff.c"
        unusual.write_text("unsigned char f(unsigned long x) { return x; }\n")
        rows = [json.loads(line) for line in run(["types", "--json-lines", "--cc", "gcc", unusual]).stdout.splitlines()]
        def has_byte_name(atom):
            return ("bytes" in atom and b"\xff" in bytes.fromhex(atom["bytes"])) or any(
                has_byte_name(item) for item in atom.get("items", []))
        if not any(has_byte_name(row["record"]) for row in rows):
            raise RuntimeError("JSON lost a non-UTF-8 source name")
        count += 1
    print("provenance controls:", count, "source/expanded reads, exact JSON records and located refusals")


if __name__ == "__main__":
    main()
