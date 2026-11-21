"""Controls for the semantic identity and dependencies of proof caching."""

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from ownership_admission import admission_projection_sha256


def plan(abi_digest="a", action="read", callback="callback-a", obligation="host-a"):
    return [
        "Ownership",
        "cesta-ownership/7",
        ["abi", "cesta-abi/2", ["sha256", abi_digest], ["layout", ["target", "test"]]],
        ["callbacks", [callback, "target"]],
        ["foreign", "host", ["signature", action], ["obligation", obligation]],
        ["function", "target", ["body", action]],
    ]


base = plan()
abi_only = plan(abi_digest="b")
assert admission_projection_sha256(base) == admission_projection_sha256(abi_only)
abi_layout_only = copy.deepcopy(base)
abi_layout_only[2][3] = ["layout", ["target", "other-native-profile"]]
assert admission_projection_sha256(base) == admission_projection_sha256(abi_layout_only)

for changed in (
    plan(action="write"),
    plan(callback="callback-b"),
    plan(obligation="host-b"),
):
    assert admission_projection_sha256(base) != admission_projection_sha256(changed)

layout_region = copy.deepcopy(base)
layout_region[-1][-1][-1] = "different-byte-region"
assert admission_projection_sha256(base) != admission_projection_sha256(layout_region)

print("PASS ownership admission cache: ABI metadata reused; proof semantics invalidate")


def command(*arguments):
    result = subprocess.run(arguments, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError((arguments, result.returncode, result.stdout, result.stderr))
    return result


def integration():
    root = Path(__file__).resolve().parent.parent
    base = (root / "ownership/examples/ElementOrigin.cesta").read_text()
    with tempfile.TemporaryDirectory(prefix="ownership-admission-cache-", dir=root / "build") as work:
        work = Path(work)
        source = work / "probe.cesta"
        output = work / "admission"

        def admit(text):
            source.write_text(text)
            command(sys.executable, "-B", root / "tests/ownership_admission.py",
                    "--cesta", root / "build/cesta", "--source", source,
                    "--output", output, "--jobs", "2")
            return json.loads((output / "result.json").read_text())[0]

        initial = admit(base)
        assert initial["cached_parts"] == 0 and initial["module_admitted"]

        changed_body = base.replace(
            "static int read_element(const int *p) { return *p; }",
            "static int read_element(const int *p) { return *p + *p; }")
        assert changed_body != base

        delta_source = work / "delta.cesta"
        delta_source.write_text(changed_body)
        delta_output = work / "delta"
        command(sys.executable, "-B", root / "tests/ownership_delta_admission.py",
                "--cesta", root / "build/cesta", "--lean-root",
                root.parent / "Mettapedia/lean/mettapedia", "--source", delta_source,
                "--base-output", output, "--output", delta_output, "--jobs", "2")
        delta = json.loads((delta_output / "result.json").read_text())
        assert delta["changed_functions"] == ["read_element"]
        assert delta["reused_functions"] == 4 and delta["module_admitted"]

        body_result = admit(changed_body)
        assert body_result["cached_parts"] == 5 and body_result["module_admitted"]
        changed = [row["name"] for row in body_result["timings"] if row["seconds"]]
        assert changed == ["read_element"], changed

        changed_signature = changed_body.replace(
            "static const int *keep_element(const int *p) { return p; }",
            "static int *keep_element(int *p) { return p; }")
        assert changed_signature != changed_body
        signature_result = admit(changed_signature)
        assert signature_result["cached_parts"] == 0 and signature_result["module_admitted"]
        assert all(row["seconds"] for row in signature_result["timings"])

    print("PASS ownership admission cache integration: one body rechecks one part, delta module reuses four, signature rechecks dependents")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--integration", action="store_true")
    args = parser.parse_args()
    if args.integration:
        integration()
