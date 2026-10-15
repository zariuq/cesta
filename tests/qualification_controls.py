"""Refusal controls for test-evidence reuse and IR comparison boundaries."""

import copy
import unittest
import tempfile
from pathlib import Path

import qualification_cache as cache
from semantic_oracles import compare_ir
from source_qualification import source_state, dependencies


class EvidenceControls(unittest.TestCase):
    def setUp(self):
        self.unit = dict(corpus="example", unit="unit", source="unit.c", configuration="core",
                         compiler=["gcc"], flags=["-O2", "-g0"], cwd="project")
        self.inputs = {"binary": "one", "tools": {"gcc": "two"}, "checker": "three"}
        self.record = cache.receipt({"qualified": True, "source_stable": True, "tree": "four"}, self.inputs, self.unit)

    def test_unchanged_receipt(self):
        self.assertTrue(cache.current(self.record, self.inputs, self.unit))

    def test_each_configuration_change_refused(self):
        for key in cache.CONTEXT:
            unit = copy.deepcopy(self.unit)
            unit[key] = ["different"] if isinstance(unit[key], list) else "different"
            self.assertFalse(cache.current(self.record, self.inputs, unit), key)

    def test_each_implementation_change_refused(self):
        for key in self.inputs:
            inputs = dict(self.inputs, **{key: "different"})
            self.assertFalse(cache.current(self.record, inputs, self.unit), key)

    def test_changed_result_refused(self):
        for key in ("qualified", "source_stable", "tree"):
            record = dict(self.record, **{key: "different"})
            self.assertFalse(cache.current(record, self.inputs, self.unit), key)

    def test_missing_or_unknown_certificate_refused(self):
        self.assertFalse(cache.current({}, self.inputs, self.unit))
        record = copy.deepcopy(self.record)
        record["certificate"]["format"] = "unknown"
        self.assertFalse(cache.current(record, self.inputs, self.unit))


class SourceEvidenceControls(unittest.TestCase):
    def test_header_location_change_invalidates_unchanged_tokens(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(dir=root / "build") as directory:
            work = Path(directory)
            (work / "unit.c").write_text('#include "included.h"\n')
            header = work / "included.h"
            header.write_text("int /* one */ value;\n")
            unit = {"cwd": str(work), "compiler": ["gcc"], "flags": ["-std=c11"], "source": "unit.c"}
            before = source_state(unit, work)
            self.assertEqual(before, source_state(unit, work))
            (work / "unrelated.h").write_text("unrelated\n")
            self.assertEqual(before, source_state(unit, work))
            header.write_text("int /* a longer comment */ value;\n")
            after = source_state(unit, work)
            for compiler in ("gcc", "clang"):
                self.assertEqual(before[compiler]["preprocessed_sha256"], after[compiler]["preprocessed_sha256"])
                self.assertNotEqual(before[compiler]["dependencies_sha256"], after[compiler]["dependencies_sha256"])

    def test_make_escaped_dependency_names(self):
        data = b"cesta-qualified: a\\ b.h c$$d.h " + bytes([92, 10]) + b" e\\#f.h\n"
        names = dependencies(data, "/project")
        self.assertEqual(names, ["/project/a b.h", "/project/c$d.h", "/project/e#f.h"])

class IRControls(unittest.TestCase):
    def call(self, align, intrinsic="memcpy", value="false"):
        return f"  call void @llvm.{intrinsic}.p0.p0.i64(ptr align {align} %a, ptr align 8 %b, i64 8, i1 {value})"

    def test_exact(self):
        evidence = compare_ir(self.call(8), self.call(8))
        self.assertTrue(evidence["identical_ir"])
        self.assertTrue(evidence["raw_identical_ir"])

    def test_alignment_weakening_recorded(self):
        evidence = compare_ir(self.call(8), self.call(1))
        self.assertTrue(evidence["identical_ir"])
        self.assertFalse(evidence["raw_identical_ir"])
        self.assertEqual(len(evidence["alignment_differences"]), 1)

    def test_alignment_strengthening_refused(self):
        self.assertFalse(compare_ir(self.call(1), self.call(8))["identical_ir"])

    def test_observable_changes_refused(self):
        for changed in (self.call(1, value="true"), self.call(1, intrinsic="memmove"),
                        self.call(1).replace("i64 8", "i64 4"), self.call(1).replace("%b", "%c"),
                        self.call(1)+"\n  ret void"):
            self.assertFalse(compare_ir(self.call(8), changed)["identical_ir"])

    def test_only_referenced_source_locations_normalized(self):
        original = '  call void asm "", ""(), !srcloc !0\n!0 = !{i64 1}\n!1 = !{i64 2}'
        self.assertTrue(compare_ir(original, original.replace("!0 = !{i64 1}", "!0 = !{i64 3}"))["identical_ir"])
        self.assertFalse(compare_ir(original, original.replace("!1 = !{i64 2}", "!1 = !{i64 3}"))["identical_ir"])


if __name__ == "__main__":
    unittest.main()
