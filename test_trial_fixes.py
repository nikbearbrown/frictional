"""Regression tests for problems found by running Frictional on real coursework (synthetic projects only)."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import frictional_cli as fc
from frictional_cli import audit_folder, last_error, main, rule_steps
from frictional_reporting import validate_report, write_instructor_notes
import process_evidence as pe


def by_id(report, name):
    return next(d for d in report["dimensions"] if d["id"] == name)


def static(root):
    return audit_folder(root, {"name": "toy", "kind": "folder", "revision": None, "snapshot": "Working directory."},
                        {}, None, None, set())


class TrialFixes(unittest.TestCase):
    def setUp(self):
        self.space = tempfile.TemporaryDirectory()
        self.addCleanup(self.space.cleanup)
        self.root = Path(self.space.name) / "project"
        self.root.mkdir()

    def test_hidden_folder_does_not_block_top_level_checks(self):
        (self.root / "app.py").write_text("print('hi')\n")
        (self.root / ".streamlit").mkdir()
        (self.root / ".streamlit" / "config.toml").write_text("[server]\n")
        (self.root / ".github").mkdir()
        report = static(self.root)
        self.assertEqual(by_id(report, "process-log")["status"], "no_evidence")
        self.assertEqual(by_id(report, "readme-completeness")["status"], "no_evidence")
        dims = {s["dimension"] for s in report["next_steps"]}
        self.assertTrue({"process-log", "readme-completeness"} <= dims)

    def test_artifact_gaps_become_next_steps(self):
        (self.root / "main.py").write_text("print(1)\n")
        steps = {s["dimension"]: s for s in static(self.root)["next_steps"]}
        self.assertEqual(steps["readme-completeness"]["priority"], "P1")
        self.assertIn("verification", steps)
        self.assertIn("documentation-licensing", steps)

    def test_readme_only_in_one_subfolder_is_named(self):
        (self.root / "app").mkdir()
        (self.root / "app" / "README.md").write_text("# App\n")
        (self.root / "app" / "main.py").write_text("print(1)\n")
        step = next(s for s in static(self.root)["next_steps"] if s["dimension"] == "readme-completeness")
        self.assertIn("inside app/", step["change"])

    def test_readme_without_install_or_usage(self):
        (self.root / "README.md").write_text("# Toy\n\n## Overview\n\nWhat it does.\n")
        (self.root / "LICENSE").write_text("MIT\n")
        steps = {s["dimension"]: s for s in static(self.root)["next_steps"]}
        self.assertEqual(steps["readme-completeness"]["priority"], "P2")
        self.assertIn("an Install section and a Usage (or Run) section", steps["readme-completeness"]["change"])
        self.assertNotIn("documentation-licensing", steps)
        self.assertNotIn("verification", steps)  # no code, so no test step

    def test_git_cli_fallback_when_builtin_reader_fails(self):
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        env = {"GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@example.invalid",
               "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@example.invalid", "HOME": self.space.name,
               "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True, env=env)
        for n in range(3):
            (self.root / f"f{n}.txt").write_text(str(n))
            subprocess.run(["git", "add", "."], cwd=self.root, check=True, env=env)
            subprocess.run(["git", "commit", "-q", "-m", f"c{n}"], cwd=self.root, check=True, env=env)
        with mock.patch.object(pe, "history", return_value=([], "unavailable")):
            found = pe.collect(self.root, {}, None, None)
        self.assertEqual(found["commit-history"]["status"], "reported")
        self.assertIn("3 verified", found["commit-history"]["facts"][0]["observation"])

    def test_guessed_command_failure_is_not_blamed_on_the_student(self):
        out = Path(self.space.name) / "out"
        (out / "run" / "logs").mkdir(parents=True)
        (out / "run" / "logs" / "setup-1.log").write_text(
            "# frictional run log\n$ python -m pip install .\nCollecting x\n"
            "ERROR: Failed to build installable wheels for some pyproject.toml based projects (toy)\n"
            "# exit code: 1  seconds: 3.0  timed out: no\n")
        receipt = {"overall": {"setup": "failed"}, "commands": [
            {"phase": "setup", "exit_code": 1, "cmd": "python -m pip install --no-cache-dir .",
             "source": "default: pyproject.toml", "log": "run/logs/setup-1.log"}]}
        steps = rule_steps([], receipt, out=out)
        self.assertEqual(steps[0]["priority"], "P1")
        self.assertIn("Frictional guessed", steps[0]["change"])
        self.assertIn("Failed to build installable wheels", steps[0]["change"])
        receipt["commands"][0]["source"] = "README.md:12"
        self.assertEqual(rule_steps([], receipt, out=out)[0]["priority"], "P0")

    def test_last_error_picks_the_cause_and_stays_inside_run_logs(self):
        out = Path(self.space.name) / "out"
        (out / "run" / "logs").mkdir(parents=True)
        (out / "run" / "logs" / "serve-1.log").write_text(
            "# frictional run log\n$ python serve.py\nTraceback (most recent call last):\n"
            "  File \"x\", line 1\nModuleNotFoundError: No module named 'toy_helpers'\n# exit code: 1\n")
        self.assertEqual(last_error(out, "run/logs/serve-1.log"), "ModuleNotFoundError: No module named 'toy_helpers'")
        self.assertIsNone(last_error(out, "../secret.txt"))

    def test_serve_failure_names_the_cause(self):
        out = Path(self.space.name) / "out"
        (out / "run" / "logs").mkdir(parents=True)
        (out / "run" / "logs" / "serve-1.log").write_text(
            "# frictional run log\n$ streamlit run app.py\nError: Invalid value: File does not exist: app.py\n# exit code: 2\n")
        receipt = {"overall": {"setup": "passed", "serve": "failed"}, "commands": [],
                   "serve": {"state": "exited", "exit_code": 2, "log": "run/logs/serve-1.log"}}
        step = rule_steps([], receipt, out=out)[0]
        self.assertIn("File does not exist: app.py", step["change"])
        self.assertIn("exited", step["change"])

    def test_nothing_to_run_says_so(self):
        steps = rule_steps([], None, run_stopped="no_spec")
        self.assertEqual(steps[0]["locator"], "frictional-run.json")
        report = static(self.root)
        report["limitations"].append("install-test: --run was requested, but nothing was run.")
        from frictional_reporting import _render
        text = _render(report)
        self.assertIn("Not run: \\-\\-run was requested", text)

    def test_blocked_reason_is_shown_beside_the_status(self):
        report = static(self.root)
        row = by_id(report, "commit-history")
        row["status"], row["facts"] = "blocked", []
        report["limitations"].append("commit-history: Local history is unavailable.")
        validate_report(report)
        from frictional_reporting import _render
        self.assertIn("commit\\-history (process): Blocked (Local history is unavailable\\.)", _render(report))

    def test_instructor_questions_follow_what_exists(self):
        report = static(self.root)
        out = Path(self.space.name) / "notes"
        out.mkdir()
        write_instructor_notes(report, [], out)
        text = (out / "instructor-notes.md").read_text()
        self.assertNotIn("entry in FRICTIONAL.md", text)
        self.assertIn("went wrong", text)

    def test_committed_virtualenv_is_not_the_project(self):
        venv = self.root / "lib" / "python3.12" / "site-packages" / "numpy" / "tests"
        venv.mkdir(parents=True)
        (venv / "test_core.py").write_text("x\n")
        files, _ = pe.inventory(self.root)
        self.assertFalse(any("site-packages" in f for f in files))


if __name__ == "__main__":
    unittest.main()
