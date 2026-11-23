"""Contract and renderer tests using synthetic evidence only."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from reporting import ReportValidationError, render_markdown, validate_report, write_reports


REPORT = {
    "schema_version": "1.0",
    "target": {"requested_ref": "main", "commit_sha": "a" * 40},
    "checks": [
        {
            "id": "tests",
            "title": "Documented test suite",
            "status": "not_assessed",
            "detail": "Repository commands were not executed.",
            "evidence": [{"path": "README.md", "line": 17, "observation": "Test command documented"}],
        },
        {
            "id": "clean-build",
            "title": "Clean build",
            "status": "blocked",
            "detail": "Build inputs not available.",
            "evidence": [],
        },
    ],
    "findings": [
        {
            "id": "reproduction",
            "title": "Missing instructions for Figure 2",
            "severity": "high",
            "confidence": "medium",
            "why_it_matters": "A second reader cannot reproduce the result from the documented inputs.",
            "evidence": [{"path": "README.md", "line": 26, "observation": "Lists Figure 2 without a command"}],
            "recommended_action": "Document environment, fixture, and exact command.",
            "acceptance_checks": ["A clean environment reproduces the published statistic."],
        }
    ],
}


class ReportingTests(unittest.TestCase):
    def test_render_and_json_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "review"
            write_reports(REPORT, out)
            machine = json.loads((out / "report.json").read_text())
            human = (out / "report.md").read_text()
            self.assertEqual(machine, REPORT)
            self.assertEqual(human, render_markdown(machine))
            self.assertIn("README.md:26", human)
            self.assertIn(r"not\_assessed", human)
            self.assertIn("blocked", human)
            self.assertIn("Acceptance checks", human)
            self.assertIn("Missing instructions for Figure 2", human)

    def test_invalid_status_duplicate_id_and_boolean_line_rejected(self):
        mutations = [
            (lambda r: r["checks"][0].update(status="unknown"), "status"),
            (lambda r: r["checks"][1].update(id="tests"), "duplicate identifier"),
            (lambda r: r["findings"][0]["evidence"][0].update(line=True), "line"),
            (lambda r: r["findings"][0].update(severity="catastrophic"), "severity"),
            (lambda r: r["findings"][0].update(confidence=" "), "confidence"),
            (lambda r: r["findings"][0].update(acceptance_checks="run it"), "acceptance_checks"),
        ]
        for mutate, error in mutations:
            with self.subTest(error=error):
                invalid = copy.deepcopy(REPORT)
                mutate(invalid)
                with self.assertRaisesRegex(ReportValidationError, error):
                    validate_report(invalid)

    def test_markdown_does_not_interpret_untrusted_title_or_observations(self):
        report = copy.deepcopy(REPORT)
        report["checks"][0]["title"] = "A\n# pretend heading"
        report["checks"][0]["evidence"] = ["<img src=x> [click](https://example.invalid) \u202eswap"]
        rendered = render_markdown(report)
        self.assertNotIn("\n# pretend heading", rendered)
        self.assertIn(r"A \# pretend heading", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("[click](", rendered)
        self.assertIn("U+202E", rendered)

    def test_empty_findings_explains_limit(self):
        report = copy.deepcopy(REPORT)
        report["findings"] = []
        self.assertIn("does not imply", render_markdown(report))

    def test_unknown_line_is_valid(self):
        report = copy.deepcopy(REPORT)
        report["checks"][0]["evidence"][0]["line"] = None
        validate_report(report)

    def test_invalid_report_never_creates_output(self):
        report = copy.deepcopy(REPORT)
        report["target"]["not_json"] = float("nan")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "review"
            with self.assertRaises(ReportValidationError):
                write_reports(report, out)
            self.assertFalse(out.exists())

    def test_output_file_symlink_does_not_overwrite_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "review"
            out.mkdir()
            other = Path(tmp) / "untouched.txt"
            other.write_text("unchanged")
            (out / "report.md").symlink_to(other)
            write_reports(REPORT, out)
            self.assertEqual(other.read_text(), "unchanged")
            self.assertFalse((out / "report.md").is_symlink())


if __name__ == "__main__":
    unittest.main()
