"""Qualify native compiler flags and source identity at the build boundary."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def run(arguments, expected=0):
    result = subprocess.run([str(word) for word in arguments], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expected:
        raise SystemExit(f"{arguments}: status {result.returncode}: {result.stderr.decode()}")
    return result


def main():
    output = ROOT / "build/driver"
    output.mkdir(parents=True, exist_ok=True)
    native = output / "native"
    native.mkdir(exist_ok=True)
    source = native / "identity.c"
    source.write_text("int asm,typeof;unsigned mix(unsigned x,unsigned y){return ((x<<1)|(y&3))^((x&y)|(x>>2));}int main(void){asm=3;typeof=4;return asm+typeof==7?0:1;}\n")
    wire = output / "identity.csexp"
    wire.write_bytes(run([BINARY, "read", ROOT / "build/CGNU.cf", source,
                         "TranslationUnit", ROOT / "grammar/CGNUC11.profile"]).stdout)
    records = []
    for compiler in ("gcc", "clang"):
        flags = ["-std=c11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror"]
        original, translated = output / (compiler + "-native.o"), output / (compiler + "-cesta.o")
        run([compiler, *flags, "-c", source, "-o", original])
        run([BINARY, "cc", "--cc", compiler, *flags, "-c", wire, "-o", translated])
        if original.read_bytes() != translated.read_bytes():
            raise SystemExit(f"{compiler}: compiler driver changes native object bytes")
        executable = output / (compiler + "-identity")
        run([BINARY, "cc", "--cc", compiler, translated, "-o", executable])
        run([executable])
        extension = output / (compiler + "-extension")
        run([BINARY, "cc", "--cc", compiler, *flags,
             ROOT / "examples/gnu-statement-expression.csexp", "-o", extension])
        if run([extension]).stdout != b"12\n":
            raise SystemExit(f"{compiler}: underscored-extension profile has changed behavior")
        # Last -std wins, as it does at the native compiler boundary.
        run([BINARY, "cc", "--cc", compiler, "-std=gnu11", *flags,
             "-c", wire, "-o", translated])
        denied = run([BINARY, "cc", "--cc", compiler, "-std=c11", "-std=gnu11",
                      "-c", wire, "-o", translated], expected=1)
        if not denied.stderr:
            raise SystemExit("profile refusal must identify the source position")
        run([BINARY, "cc", "--cc", compiler, "-std=c99", "-c", source, "-o", original])
        run([BINARY, "cc", "--cc", compiler, "-std=c99", "-c", wire, "-o", translated], expected=1)
        records.append({"compiler": compiler, "identical_object_sha256": hashlib.sha256(translated.read_bytes()).hexdigest(),
                        "c11_identifiers": True, "c11_extension": True,
                        "standard_last_wins": True, "native_c99_passthrough": True})
    (output / "summary.json").write_text(json.dumps(records, indent=2) + "\n")
    print("compiler driver: both compilers preserve native object bytes, flags, linking and C11 extension policy")


if __name__ == "__main__":
    main()
