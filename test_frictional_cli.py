import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from frictional_cli import ARTIFACT, PROCESS, audit_folder, main, verification_record
from frictional_reporting import validate_report


def by_id(report, name):
    return next(d for d in report["dimensions"] if d["id"] == name)


class FrictionalIntegration(unittest.TestCase):
    def setUp(self):
        self.space = tempfile.TemporaryDirectory()
        self.addCleanup(self.space.cleanup)
        self.room = Path(self.space.name)
        self.target = self.room / "synthetic"
        self.target.mkdir()
        (self.target / "README.md").write_text("# Toy\n\n## Install\n\n## Usage\n")
        (self.target / "test_example.py").write_text("raise RuntimeError('never execute target')\n")

    def test_complete_static_report_and_render(self):
        out = self.room / "result"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["audit", str(self.target), "--out", str(out)]), 0)
            self.assertEqual(main(["validate", str(out / "report.json")]), 0)
            self.assertEqual(main(["render", str(out / "report.json"), "--out", str(self.room / "new")]), 0)
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(set(d["id"] for d in report["dimensions"]), set(ARTIFACT) | set(PROCESS))
        self.assertEqual(by_id(report, "verification")["status"], "reported")
        self.assertEqual(by_id(report, "outside-in-workflow")["status"], "not_assessed")
        self.assertEqual(by_id(report, "transfer")["status"], "no_evidence")
        self.assertNotIn("never execute target", (out / "report.md").read_text())

    def test_blocked_packet_and_profile_do_not_abort(self):
        path = self.room / "evidence.json"
        path.write_text('{"retrieval":{"unexpected":true},"transfer":{"observations":[]}}')
        profile = self.room / "profile.json"
        profile.write_text('{"hide":["transfer"]}')
        out = self.room / "reports"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["audit", str(self.target), "--out", str(out),
                                   "--evidence", str(path), "--profile", str(profile)]), 0)
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(by_id(report, "retrieval")["status"], "blocked")
        self.assertTrue(any(x.startswith("retrieval:") for x in report["limitations"]))
        self.assertEqual(by_id(report, "transfer")["status"], "hidden_by_instructor")

    def test_target_is_not_mutated_and_writer_does_not_overwrite(self):
        inside = self.target / "never"
        existing = self.room / "existing"
        existing.mkdir()
        keep = existing / "keep"
        keep.write_text("unchanged")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["audit", str(self.target), "--out", str(inside)]), 2)
            self.assertEqual(main(["audit", str(self.target), "--out", str(existing)]), 2)
        self.assertFalse(inside.exists())
        self.assertEqual(keep.read_text(), "unchanged")

    def test_same_artifact_different_verification_evidence(self):
        revision = "a" * 40
        good = {"id": "confirm", "revision": revision, "covers_main_path": True, "outcome": "passed"}
        weak = {"id": "initial", "revision": revision, "covers_main_path": False, "outcome": "passed"}
        meta = {"kind": "folder", "name": "synthetic", "revision": revision}
        cases = [
            {"claimed_run": "confirm", "runs": [weak, good]},
            {"claimed_run": None, "runs": []},
            {"claimed_run": "initial", "runs": [weak]},
            {"claimed_run": "confirm", "runs": [good]},
        ]
        reports = [audit_folder(self.target, meta, {"verification-record": case}, None, None, set())
                   for case in cases]
        for report in reports:
            validate_report(report)
            self.assertEqual([d for d in report["dimensions"] if d["family"] == "artifact"],
                             [d for d in reports[0]["dimensions"] if d["family"] == "artifact"])
        records = [by_id(report, "verification-record") for report in reports]
        self.assertIn("Supported by the supplied record", records[0]["facts"][0]["observation"])
        self.assertEqual(records[1]["status"], "no_evidence")
        self.assertIn("does not cover", records[2]["facts"][0]["observation"])
        self.assertEqual(records[0], records[3])

    def test_stale_receipt_and_invalid_record(self):
        packet = {"claimed_run": "one", "runs": [
            {"id": "one", "revision": "b"*40, "covers_main_path": True, "outcome": "passed"}]}
        self.assertIn("different Git revision", verification_record(packet, "a"*40)["facts"][0]["observation"])
        packet["runs"][0]["raw_log"] = "not part of the contract"
        with self.assertRaises(ValueError):
            verification_record(packet, "a"*40)


if __name__ == "__main__":
    unittest.main()
