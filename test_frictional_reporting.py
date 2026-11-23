"""Synthetic reports only: no student records or project execution."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frictional_reporting import validate_report, write_reports


def fixture():
    return {
        "schema_version": 1, "skill_version": "1.0.0", "created_at": "2026-11-23",
        "target": {"kind": "synthetic", "label": "Test"},
        "model": {"name": None, "version": None},
        "dimensions": [
            {"id": "verification", "family": "artifact", "version": 1,
             "status": "reported",
             "facts": [{"observation": "[unsafe](javascript:bad)\n# heading <img>",
                        "locator": "file.txt:1", "alternative_explanations": ["Local change"]}],
             "interpretations": []},
            {"id": "reflection", "family": "process", "version": 1,
             "status": "no_evidence", "facts": [], "interpretations": []},
        ],
        "limitations": ["Synthetic data only."],
    }


class ReportTests(unittest.TestCase):
    def test_render_and_json(self):
        report = fixture()
        with TemporaryDirectory() as temp:
            out = Path(temp) / "new"
            json_path, markdown_path = write_reports(report, out)
            self.assertEqual(report, json.loads(json_path.read_text()))
            rendered = markdown_path.read_text()
            self.assertIn("## Observed facts", rendered)
            self.assertIn("## Model-assisted interpretations", rendered)
            self.assertIn("does not imply an absence of learning", rendered)
            self.assertNotIn("[unsafe](", rendered)
            self.assertNotIn("<img>", rendered)
            self.assertNotIn("\n# heading", rendered)
            self.assertIn(r"\[unsafe\]", rendered)
            self.assertNotIn("Test", rendered)
            with self.assertRaises(ValueError):
                write_reports(report, out)
            self.assertEqual(rendered, markdown_path.read_text())

    def test_report_validation_and_opaque_errors(self):
        for change in (
            {"schema_version": True}, {"grade": "A"}, {"limitations": [7]},
            {"target": {"raw_log": "DO-NOT-PRINT"}}, {"target": {"x": float("nan")}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError) as exception:
                validate_report({**fixture(), **change})
            self.assertNotIn("DO-NOT-PRINT", str(exception.exception))

    def test_interpretations_require_provenance(self):
        report = fixture()
        report["dimensions"][0]["interpretations"] = [
            {"observation": "One possible reading", "locator": "readme.md:2",
             "alternative_explanations": ["Different recording practice"]},
        ]
        with self.assertRaisesRegex(ValueError, "model name and version"):
            validate_report(report)
        report["model"] = {"name": "test model", "version": "v1"}
        validate_report(report)

    def test_non_reported_cannot_claim_evidence_and_ids_unique(self):
        report = fixture()
        report["dimensions"][0]["status"] = "hidden_by_instructor"
        with self.assertRaisesRegex(ValueError, "only a reported dimension"):
            validate_report(report)
        report = fixture()
        report["dimensions"][1]["id"] = report["dimensions"][0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicate dimension id"):
            validate_report(report)

    def test_refuses_existing_unrelated_file_and_symlink(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / "out"
            existing.mkdir()
            (existing / "guard").write_text("keep")
            with self.assertRaises(ValueError):
                write_reports(fixture(), existing)
            self.assertEqual(["guard"], [p.name for p in existing.iterdir()])
            self.assertEqual("keep", (existing / "guard").read_text())
            link = root / "link"
            link.symlink_to(existing, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_reports(fixture(), link)
            self.assertEqual(["guard"], [p.name for p in existing.iterdir()])

    def test_invalid_report_leaves_no_output(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "out"
            with self.assertRaises(ValueError):
                write_reports({}, out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
