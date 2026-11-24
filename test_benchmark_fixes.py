"""Regression tests for the five problems the synthetic benchmark found (Paper 1 benchmark, 2026-11-24).

All fixtures are made up for these tests; none comes from student work.
"""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from frictional_cli import audit_folder, last_error, rule_steps

TARGET = {"name": "toy", "kind": "folder", "revision": None, "snapshot": "Working directory."}


def steps_for(root, **kw):
    return audit_folder(root, TARGET, {}, None, None, set(), **kw)["next_steps"]


class BenchmarkFixes(unittest.TestCase):
    def setUp(self):
        self.space = tempfile.TemporaryDirectory()
        self.addCleanup(self.space.cleanup)
        self.root = Path(self.space.name) / "project"
        self.root.mkdir()
        self.out = Path(self.space.name) / "out"
        (self.out / "run" / "logs").mkdir(parents=True)

    def log(self, text, name="setup-1.log"):
        (self.out / "run" / "logs" / name).write_text(text)
        return f"run/logs/{name}"

    # 1. A hand-in with no code is not asked for run commands.
    def test_no_code_hand_in_gets_no_run_step(self):
        (self.root / "report.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        (self.root / "demo.mp4").write_bytes(b"\x00" * 64)
        with mock.patch("container_run.find_runtime", return_value=object()):
            steps = steps_for(self.root, run={"spec": None, "history": 0}, out=self.out)
        self.assertFalse([s for s in steps if s["dimension"] == "install-test"])

    def test_code_without_manifest_still_gets_run_step(self):
        (self.root / "analysis.ipynb").write_text('{"cells": [], "nbformat": 4, "nbformat_minor": 5}\n')
        with mock.patch("container_run.find_runtime", return_value=object()):
            steps = steps_for(self.root, run={"spec": None, "history": 0}, out=self.out)
        self.assertEqual([s["priority"] for s in steps if s["dimension"] == "install-test"], ["P1"])

    # 2. An install heading counts only when a command sits under it.
    def readme_steps(self, text):
        (self.root / "README.md").write_text(text)
        (self.root / "main.py").write_text("print(1)\n")
        return [s for s in steps_for(self.root) if s["dimension"] == "readme-completeness"]

    def test_getting_started_without_commands_is_not_an_install_section(self):
        step = self.readme_steps("# toy\n\n## Getting started\n\nEdit the files to begin.\n\n## Usage\n\nOpen it.\n")
        self.assertEqual(len(step), 1)
        self.assertIn("an Install section", step[0]["change"])

    def test_install_heading_with_a_fenced_command_counts(self):
        text = "# toy\n\n## Install\n\n```sh\npython -m pip install -r requirements.txt\n```\n\n## Usage\n\nRun it.\n"
        self.assertEqual(self.readme_steps(text), [])

    def test_inline_command_and_subsection_count(self):
        text = "# toy\n\n## Setup\n\n### Python\n\nRun `pip install -r requirements.txt` first.\n\n## Usage\n\nRun it.\n"
        self.assertEqual(self.readme_steps(text), [])

    def test_a_command_in_a_later_section_does_not_count_for_install(self):
        text = "# toy\n\n## Installation\n\nSee the course notes.\n\n## Usage\n\n```sh\npython main.py\n```\n"
        step = self.readme_steps(text)
        self.assertEqual(len(step), 1)
        self.assertIn("an Install section", step[0]["change"])
        self.assertNotIn("Usage", step[0]["change"])

    # 3. An unreadable history gets a next step.
    def test_blocked_history_gets_a_step(self):
        dims = [{"id": "commit-history", "status": "blocked", "facts": []}]
        step = next(s for s in rule_steps(dims, None) if s["dimension"] == "commit-history")
        self.assertIn("could not read the history", step["change"])
        self.assertEqual(step["priority"], "P2")

    # 4. The line that closes a traceback is quoted, even when its class is not called ...Error.
    def test_traceback_closing_line_is_quoted(self):
        log = self.log("# frictional run log\n$ python -m pip install -r requirements.txt\nCollecting toylib==1.0\n"
                       "ERROR: Exception:\nTraceback (most recent call last):\n  File \"pip/x.py\", line 9, in run\n"
                       "    raise BackendGone(\ntoy._impl.BackendGone: Cannot import 'toybuild.meta'\n# exit code: 2\n")
        self.assertEqual(last_error(self.out, log), "toy._impl.BackendGone: Cannot import 'toybuild.meta'")

    # 5. A pytest collection error quotes the cause, not the bare summary line.
    def test_pytest_collection_error_quotes_the_cause(self):
        log = self.log("# frictional run log\n$ python -m pytest -q\n"
                       "_______ ERROR collecting tests/test_toy.py _______\n"
                       "ImportError while importing test module '/work/tests/test_toy.py'.\nTraceback:\n"
                       "tests/test_toy.py:1: in <module>\n    import toyhelpers\n"
                       "E   ModuleNotFoundError: No module named 'toyhelpers'\n"
                       "=========================== short test summary info ============================\n"
                       "ERROR tests/test_toy.py\n!!!!!!!! Interrupted: 1 error during collection !!!!!!!!\n"
                       "1 error in 0.05s\n# exit code: 2\n", "test-1.log")
        self.assertEqual(last_error(self.out, log), "ModuleNotFoundError: No module named 'toyhelpers'")

    def test_failed_test_summary_is_still_quoted(self):
        log = self.log("# frictional run log\n$ python -m pytest -q\n..F\n"
                       "E       assert 3 == 4\nFAILED tests/test_toy.py::test_add - assert 3 == 4\n"
                       "1 failed, 2 passed in 0.02s\n# exit code: 1\n", "test-1.log")
        self.assertEqual(last_error(self.out, log), "1 failed, 2 passed in 0.02s")

    def test_summary_with_a_message_is_kept(self):
        log = self.log("# frictional run log\nE   RuntimeError: TOY_KEY is not set\n"
                       "ERROR tests/test_toy.py - RuntimeError: TOY_KEY is not set\n1 error in 0.1s\n", "test-1.log")
        self.assertEqual(last_error(self.out, log), "ERROR tests/test_toy.py - RuntimeError: TOY_KEY is not set")


if __name__ == "__main__":
    unittest.main()
