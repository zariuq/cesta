"""Check C payload bytes independently by compiling and running both codecs."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def run(arguments):
    done = subprocess.run([str(a) for a in arguments], cwd=ROOT, capture_output=True)
    if done.returncode or done.stderr:
        raise SystemExit(f"{arguments}: status {done.returncode}: {done.stderr.decode()}")
    return done.stdout


def main():
    destination = ROOT / "build/literal-bytes"
    destination.mkdir(parents=True, exist_ok=True)
    original, printed = destination / "baseline", destination / "printed"
    original.mkdir(exist_ok=True)
    printed.mkdir(exist_ok=True)
    escapes = [f"\\{byte:03o}" for byte in range(256)]
    string = '"' + ''.join(escapes) + '"'
    characters = ','.join(f"(unsigned char)'{escape}'" for escape in escapes)
    source = original / "payload.c"
    source.write_text("extern int putchar(int);\n"
                      f"static const unsigned char text[]={string};\n"
                      f"static const unsigned char chars[]={{ {characters} }};\n"
                      "int main(void){for(unsigned i=0;i<256;++i)"
                      "if(text[i]!=i||chars[i]!=i)return 1;"
                      "putchar('O');putchar('K');putchar('\\n');return 0;}\n")
    binary = ROOT / "build/cesta"
    grammar, profile = ROOT / "build/C11.cf", ROOT / "grammar/C11.profile"
    wire = destination / "payload.csexp"
    wire.write_bytes(run([binary, "read", grammar, source, "TranslationUnit", profile]))
    emitted = printed / "payload.c"
    emitted.write_bytes(run([binary, "write", grammar, wire, "TranslationUnit", profile]))
    if run([binary, "read", grammar, emitted, "TranslationUnit", profile]) != wire.read_bytes():
        raise SystemExit("literal spelling or sorted tree changed across the codecs")
    records = []
    for compiler in ("gcc", "clang"):
        objects = []
        for directory in (original, printed):
            obj = directory / (compiler + ".o")
            run([compiler, "-std=c11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror",
                 "-c", directory / "payload.c", "-o", obj])
            objects.append(obj.read_bytes())
        if objects[0] != objects[1]:
            raise SystemExit(f"{compiler}: literal codec changes object bytes")
        executable = destination / compiler
        run([compiler, printed / (compiler + ".o"), "-o", executable])
        if run([executable]) != b"OK\n":
            raise SystemExit(f"{compiler}: C string/character payload bytes changed")
        records.append({"compiler": compiler, "string_bytes": 256, "character_bytes": 256,
                        "literal_spelling_preserved": True, "tree_roundtrip": True,
                        "object_sha256": hashlib.sha256(objects[0]).hexdigest(), "output": "OK\n"})
    (destination / "summary.json").write_text(json.dumps(records, indent=2) + "\n")
    print("literal payloads: all 256 string and character bytes preserved; both compiler objects identical and executions correct")


if __name__ == "__main__":
    main()
