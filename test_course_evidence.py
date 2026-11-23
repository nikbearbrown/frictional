"""Synthetic tests for the course process files and the v2 report. No student work."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from course_evidence import collect_course, parse_date
from frictional_cli import main
from frictional_reporting import validate_report

LOG = """# Frictional

## Dated entry

Date (mark retrospective entries): 2026-11-18
What I tried and expected: Split on spaces and expected three words.
What happened / what I found difficult or checked: Tabs were not separators at first.
What I did next: Switched to split() and reran the sample.
What Claude or another person contributed: Claude suggested split(); I checked tabs myself.
What I understand now / still do not understand: Unicode spaces are still unclear.
Evidence, commit, and next step: commit abc1234, then add tests.

## Dated entry

Date (mark retrospective entries): 2026-11-20 (retrospective)
What I tried and expected: Add a test file.
What happened / what I found difficult or checked:
What I did next:
What Claude or another person contributed: none
What I understand now / still do not understand:
Evidence, commit, and next step:
"""

PRED = """# Predictions
## Before the run
Expected result: 3
Confidence and reason: 80% simple file
## After the run
Actual output and evidence path: 3
Prediction held: yes

## Before the run
Expected result: tabs count
Confidence and reason: 90%
## After the run
Actual output and evidence path: they did not
Prediction held: no
"""


def write(folder, name, text):
    (folder / name).write_text(text)
    return name


class CourseFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def collect(self, rows=()):
        files = {p.relative_to(self.root).as_posix(): p.stat().st_size for p in self.root.rglob("*") if p.is_file()}
        return collect_course(self.root, files, list(rows))

    def test_absent_files_are_no_evidence(self):
        out = self.collect()
        self.assertTrue(all(v == {"status": "no_evidence", "facts": []} for v in out.values()))

    def test_log_rows_counts_and_no_text(self):
        write(self.root, "FRICTIONAL.md", LOG)
        out = self.collect()["log-rubric-rows"]
        self.assertEqual(out["status"], "reported")
        text = out["facts"][0]["observation"]
        self.assertIn("2 entries (2 dated, 1 marked retrospective, 0 without template labels)", text)
        self.assertIn("Friction and response 1/2", text)
        self.assertIn("Human and AI contributions 2/2", text)
        self.assertNotIn("Unicode", json.dumps(out))  # no student sentences
        self.assertTrue(any("line 13" in n for n in out["notes"]))
        self.assertTrue(all(s["priority"] in {"P1", "P2"} and s["check"] for s in out["next_steps"]))

    def test_blank_template_entry_counts_as_unfilled(self):
        write(self.root, "FRICTIONAL.md", "# Frictional\n\n## Dated entry\n\nDate (mark retrospective entries):\n"
                                          "What I tried and expected:\nWhat happened / what I found difficult or checked:\n")
        out = self.collect()["log-rubric-rows"]
        self.assertIn("Attempts 0/1", out["facts"][0]["observation"])

    def test_commit_consistency(self):
        write(self.root, "FRICTIONAL.md", LOG)
        day = lambda d: dt.datetime(2026, 11, d, 12, tzinfo=dt.timezone.utc)
        rows = [("a" * 40, day(25), day(25), b"x", False), ("b" * 40, day(18), day(18), b"x", False)]
        out = self.collect(rows)["log-commit-consistency"]
        self.assertIn("Log dates within one day of a commit to this folder: 1/2", out["facts"][0]["observation"])
        self.assertIn("Commit days within one day of a log entry: 1/2", out["facts"][0]["observation"])
        self.assertTrue(any("2026-11-25" in n for n in out["notes"]))

    def test_predictions_calibration_arithmetic(self):
        write(self.root, "PREDICTIONS.md", PRED)
        text = self.collect()["prediction-log"]["facts"][0]["observation"]
        self.assertIn("2 prediction(s)", text)
        self.assertIn("mean confidence 0.85", text)
        self.assertIn("share that held 0.50", text)
        self.assertIn("mean squared error 0.425", text)
        self.assertIn("+0.35", text)

    def test_predictions_in_words_get_a_next_step(self):
        write(self.root, "PREDICTIONS.md", "## Before the run\nExpected result: 3\nConfidence and reason: fairly sure\n")
        out = self.collect()["prediction-log"]
        self.assertIn("(0 as a number)", out["facts"][0]["observation"])
        self.assertTrue(any("as a number" in s["change"] for s in out["next_steps"]))

    def test_verification_and_contributions(self):
        write(self.root, "VERIFICATION.md", "Candidate version or content identifier: commit aaaaaaa\n"
                                            "Checks that failed and subsequent revision: the tab test failed; fixed split\n")
        write(self.root, "CONTRIBUTIONS.md", "What Claude contributed (model identifier as displayed, date, prompt excerpt): "
                                             "Claude Opus 5.5 suggested split\nWhat I accepted, changed, or rejected, and why: accepted after a test\n")
        rows = [("a" * 40, dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc),
                 dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc), b"x", False)]
        out = self.collect(rows)
        v = out["verification-notes"]["facts"][0]["observation"]
        self.assertIn("checks that failed and the revision that followed: filled", v)
        self.assertIn("candidate commit found", v)
        c = out["contributions-log"]["facts"][0]["observation"]
        self.assertIn("model named: yes", c)

    def test_templates_folder_is_ignored(self):
        (self.root / "templates").mkdir()
        write(self.root / "templates", "FRICTIONAL.md", LOG)
        self.assertEqual(self.collect()["log-rubric-rows"]["status"], "no_evidence")

    def test_parse_date_formats(self):
        self.assertEqual(parse_date("Nov 20, 2026"), dt.date(2026, 11, 20))
        self.assertEqual(parse_date("20 November 2026"), dt.date(2026, 11, 20))
        self.assertEqual(parse_date("11/20/2026"), dt.date(2026, 11, 20))
        self.assertIsNone(parse_date("no date here"))


class ReportV2(unittest.TestCase):
    def test_student_folder_in_course_repo_and_instructor_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "course"
            folder = repo / "fall-2026" / "someone" / "assignment-01"
            folder.mkdir(parents=True)
            write(folder, "README.md", "# Toy\n\n## Install\n\n## Usage\n")
            write(folder, "FRICTIONAL.md", LOG)
            env = {"GIT_AUTHOR_DATE": "2026-11-18T10:00:00Z", "GIT_COMMITTER_DATE": "2026-11-18T10:00:00Z",
                   "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "HOME": tmp}
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "first"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
            out = Path(tmp) / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["audit", str(folder), "--out", str(out), "--mode", "instructor"]), 0)
            report = json.loads((out / "report.json").read_text())
            validate_report(report)
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(report["mode"], "instructor")
            self.assertEqual([r["id"] for r in report["glp"]], [f"Y{i}" for i in range(1, 8)])
            status = {d["id"]: d["status"] for d in report["dimensions"]}
            self.assertEqual(status["commit-history"], "reported")
            self.assertEqual(status["log-rubric-rows"], "reported")
            self.assertIsNone(report["run"])
            self.assertTrue((out / "instructor-notes.md").exists())
            self.assertNotIn("Where to look", (out / "report.md").read_text())
            self.assertEqual(report["target"]["folder_in_repository"], "fall-2026/someone/assignment-01")

    def test_student_mode_has_no_instructor_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "p"
            folder.mkdir()
            write(folder, "README.md", "# Toy\n")
            out = Path(tmp) / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["audit", str(folder), "--out", str(out)]), 0)
            self.assertFalse((out / "instructor-notes.md").exists())
            report = json.loads((out / "report.json").read_text())
            self.assertTrue(any(s["dimension"] == "process-log" and s["priority"] == "P1" for s in report["next_steps"]))

    def test_run_options_need_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(main(["audit", tmp, "--out", str(Path(tmp) / ".." / "x-out"), "--run-history", "3"]), 2)
            self.assertIn("need --run", err.getvalue())


if __name__ == "__main__":
    unittest.main()
