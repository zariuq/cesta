"""Names hidden in native headers, macro expansion and compiler flags."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-native-name-tests"
MODULE = ROOT / "tests/modules/MacroControls.module"


def run(arguments, cwd, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\n{result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-5000:])
    return result


def document(header, expression, after=""):
    return '(cesta native-c/1 compact-c/2 (unit (include ' + json.dumps(header) + ') ' \
        '(use-macros ' + json.dumps(str(MODULE)) + ' Arithmetic) ' \
        '(fn int main (void) (return (!= ' + expression + ' 131))) ' + after + '))\n'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    records = []
    for compiler in ("gcc", "clang"):
        project = WORK / compiler; project.mkdir(exist_ok=True)
        cases = {
            "hidden-variable": "int cesta_macro_1 = 123;\n#define HIDDEN_VALUE() cesta_macro_1\n",
            "pasted-variable": "int cesta_macro_1 = 123;\n#define CAT0(a,b) a ## b\n"
                               "#define CAT(a,b) CAT0(a,b)\n#define HIDDEN_VALUE() CAT(cesta_macro_,1)\n",
            "cpp-name": "#define cesta_macro_0 ignored_cpp_name\n"
                        "int cesta_macro_1 = 123;\n#define HIDDEN_VALUE() cesta_macro_1\n",
            "early-undef": "int cesta_macro_1 = 123;\n#define HIDDEN_VALUE() cesta_macro_1\n",
            "flag-branch": "#ifdef SELECT_OTHER\nint cesta_macro_3=123;\n#define HIDDEN_VALUE() cesta_macro_3\n"
                           "#else\nint cesta_macro_1=123;\n#define HIDDEN_VALUE() cesta_macro_1\n#endif\n",
        }
        for name, header in cases.items():
            (project / (name + ".h")).write_text(header)
            source = project / (name + ".cesta")
            after = "(undef HIDDEN_VALUE)" if name == "early-undef" else ""
            source.write_text(document(name + ".h", "(ordered-sum 8 (call HIDDEN_VALUE))", after))
            flags = ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror"]
            if name == "flag-branch": flags += ["-DSELECT_OTHER"]
            executable = project / name
            run([BINARY, "cc", "--cc", compiler, *flags, "-MMD", "-MP", source, "-o", executable], project)
            run([executable], project)
            expanded = project / (name + ".c")
            run([BINARY, "expand", "--cc", compiler, source, "-o", expanded, "--", *flags], project)
            standalone = project / (name + "-exported")
            run([compiler, *flags, expanded, "-o", standalone], project)
            run([standalone], project)
            # Export preserves includes and macro calls as authored; compiler
            # name admission is internal build data.
            text = expanded.read_text()
            assert '#include "' + name + '.h"' in text
            assert "HIDDEN_VALUE" in text
            assert "#define __GNUC__" not in text
            records.append({"compiler": compiler, "case": name, "exported_C": True})
        print(compiler + ": hidden header names, pasted names, undef and flag contexts passed", flush=True)
    (WORK / "summary.json").write_text(json.dumps({"records": records,
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
