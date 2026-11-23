"""Synthetic worktrees and packets only; no project code or Git execution."""
import hashlib
import json
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

import process_evidence as pe


class ProcessEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "target"
        self.root.mkdir()

    def write(self, name, content):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def loose(self, timestamp, author="Secret Name <personal@example.invalid>", parent=None, committer=None):
        header = (
            "tree " + "a" * 40 + "\n" +
            (f"parent {parent}\n" if parent else "") +
            f"author {author} {timestamp} +0000\n" +
            f"committer {author} {committer if committer is not None else timestamp} +0000\n\n"
        )
        content = (header + "SENSITIVE SOURCE COMMIT MESSAGE\n").encode()
        obj = f"commit {len(content)}\0".encode() + content
        oid = hashlib.sha1(obj).hexdigest()
        dest = self.root / ".git" / "objects" / oid[:2] / oid[2:]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zlib.compress(obj))
        return oid

    def run_collect(self, supplied=None, starter=None, deadline=None):
        # Any production attempt to start a subprocess fails the test.
        with patch("subprocess.Popen", side_effect=AssertionError("execution was attempted")):
            return pe.collect(self.root, supplied, starter, deadline)

    def test_empty(self):
        out = self.run_collect()
        self.assertEqual(tuple(out), pe.DIMENSIONS)
        for k in out.keys() - {"repo-hygiene"}:
            self.assertEqual(out[k], {"status": "no_evidence", "facts": []}, k)
        self.assertEqual(out["repo-hygiene"]["status"], "reported")

    def test_history_and_log_no_personal_contents(self):
        a = self.loose(1735689600)
        b = self.loose(1735696800, parent=a, committer=1735700400)
        self.write(".git/HEAD", "ref: refs/heads/main\n")
        self.write(".git/refs/heads/main", b)
        self.write("FRICTIONAL.md", f"# 2025-01-01 internal name\ncommit {a[:10]}\ncommit 1234567890\n")
        self.write(".git/config", "[core]\nfsmonitor = ./unapproved-command\n")
        out = self.run_collect(deadline="2025-01-02T00:00:00Z")
        h = json.dumps(out)
        self.assertIn("2 verified HEAD first-parent", h)
        self.assertIn("2 author-date cluster", h)
        self.assertIn("1 author/committer timestamp difference", h)
        self.assertIn("1/2 distinct commit-reference", h)
        for secret in ("Secret Name", "personal@", "SENSITIVE SOURCE", a, "internal name", "unapproved-command"):
            self.assertNotIn(secret, h)

    def test_partial_and_missing_or_packed_history(self):
        p = self.loose(1735689600, parent="b"*40)
        self.write(".git/HEAD", p)
        self.assertIn("coverage: partial", json.dumps(self.run_collect()["commit-history"]))
        self.write(".git/HEAD", "c"*40)
        self.assertEqual(self.run_collect()["commit-history"]["status"], "blocked")

    def test_packed_history_and_delta_without_git(self):
        parent = self.loose(1735689600)
        child = self.loose(1735696800, parent=parent)
        def body(oid):
            p = self.root / ".git" / "objects" / oid[:2] / oid[2:]
            content = zlib.decompress(p.read_bytes()).split(b"\0", 1)[1]
            p.unlink()
            return content
        a, b = body(parent), body(child)
        def header(kind, size):
            first = (kind << 4) | (size & 15)
            size >>= 4
            ans = bytearray()
            while size:
                ans.append(first | 128)
                first, size = size & 127, size >> 7
            ans.append(first)
            return ans
        def varint(n):
            if n < 128:
                return bytes([n])
            return bytes([(n & 127) | 128]) + varint(n >> 7)
        def insert_only(chunk):
            return b"".join(bytes([len(chunk[i:i+100])]) + chunk[i:i+100]
                            for i in range(0, len(chunk), 100))
        e1 = header(1, len(a)) + zlib.compress(a)
        d = varint(len(a)) + varint(len(b)) + insert_only(b)
        e2 = header(7, len(d)) + bytes.fromhex(parent) + zlib.compress(d)
        entries = sorted([(bytes.fromhex(parent), 12, e1), (bytes.fromhex(child), 12+len(e1), e2)])
        pack = b"PACK" + struct.pack("!II", 2, len(entries)) + e1 + e2
        pack_hash = hashlib.sha1(pack).digest()
        pack += pack_hash
        fanout = [sum(oid[0] <= i for oid, _, _ in entries) for i in range(256)]
        idx = b"\xfftOc\0\0\0\x02" + struct.pack("!256I", *fanout)
        idx += b"".join(oid for oid, _, _ in entries)
        idx += b"".join(struct.pack("!I", zlib.crc32(blob)) for _, _, blob in entries)
        idx += b"".join(struct.pack("!I", off) for _, off, _ in entries)
        idx += pack_hash
        idx += hashlib.sha1(idx).digest()
        name = self.root / ".git" / "objects" / "pack" / ("pack-" + pack_hash.hex())
        name.parent.mkdir()
        name.with_suffix(".idx").write_bytes(idx)
        name.with_suffix(".pack").write_bytes(pack)
        self.write(".git/HEAD", "ref: refs/heads/main\n")
        self.write(".git/packed-refs", f"# pack-refs\n{child} refs/heads/main\n")
        out = self.run_collect()
        self.assertIn("2 verified HEAD first-parent", json.dumps(out))
        name.with_suffix(".pack").write_bytes(pack[:-1] + bytes([pack[-1] ^ 1]))
        self.assertEqual(self.run_collect()["commit-history"]["status"], "blocked")

    def test_install_metadata_is_normalized_and_bound_to_observed_revision(self):
        head = self.loose(1735689600)
        self.write(".git/HEAD", head)
        receipt = {"attested": True, "isolated": True, "outcome": "passed", "elapsed_seconds": 0,
                   "revision": head, "environment": "sandbox", "receipt_sha256": "7"*64}
        actual = json.dumps(self.run_collect({"install-test": receipt})["install-test"])
        self.assertIn("local revision matched", actual)
        self.assertIn("reported environment: sandbox", actual)
        self.assertNotIn(head, actual)
        self.assertNotIn("7"*64, actual)
        receipt["revision"] = "7"*40
        self.assertEqual(self.run_collect({"install-test": receipt})["install-test"]["status"], "blocked")

    def test_symlinks_hardlinks_and_hygiene(self):
        original = self.write("a-visible.txt", "PRIVATE CONTENT")
        (self.root / "README.md").symlink_to(original)
        (self.root / "external").symlink_to(Path(self.temp.name), target_is_directory=True)
        (self.root / "FRICTIONAL.md").hardlink_to(original)
        self.write("my-secret.txt", "TOPSECRET")
        self.write("CamelCase.py", "raise SystemExit('never run')")
        self.write(".gitignore", "dist")
        self.write("dist/ignored.py", "raise SystemExit('never run')")
        out = self.run_collect()
        raw = json.dumps(out)
        self.assertIn("Symlinks skipped: 2", raw)
        self.assertIn("credential-like names skipped: 1", raw)
        self.assertIn('1/1 inspected Python', raw)
        self.assertEqual(out["readme-completeness"]["status"], "blocked")
        self.assertEqual(out["process-log"]["status"], "blocked")
        self.assertNotIn("TOPSECRET", raw)
        self.assertNotIn("CamelCase.py", raw)

    def test_template_and_readme(self):
        self.write("README.md", "# Overview\n## Install\n```\n# Test\n```\n")
        self.write("source.py", "# code only")
        self.write("extra.py", "# extra")
        self.write(".env", "private")
        starter = Path(self.temp.name) / "starter"
        starter.mkdir()
        (starter / "README.md").write_text("different")
        (starter / "source.py").write_text("# code only")
        (starter / ".env").write_text("different private")
        out = self.run_collect(starter=starter)
        raw = json.dumps(out)
        self.assertIn("heading cues: purpose, install.", raw)
        self.assertIn("identical bytes in 1/2", raw)
        self.assertIn("target-only 1, starter-only 0, shared 2", raw)

    def test_external_receipt_is_not_execution(self):
        rs = {"attested": True, "isolated": True, "outcome": "failed", "elapsed_seconds": 3.5}
        out = self.run_collect({"install-test": rs})
        self.assertEqual(out["install-test"]["status"], "reported")
        self.assertIn("No commands executed", json.dumps(out))
        for unsafe in (dict(rs, command="do not run"), dict(rs, isolated=False), dict(rs, elapsed_seconds=float("inf"))):
            out = self.run_collect({"install-test": unsafe})
            self.assertEqual(out["install-test"]["status"], "blocked")
            self.assertNotIn("do not run", json.dumps(out))

    def test_structured_positive_and_limits(self):
        a, b = "2025-01-01T01:00:00Z", "2025-01-01T03:00:00Z"
        rs = {
            "push-timeline": {"events": [{"at": b, "kind": "ci"}, {"at": a, "kind": "push"}]},
            "error-trajectory": {"events": [{"at": a, "outcome": "fail", "category": "config"}, {"at": b, "outcome": "pass"}]},
            "temporal-engagement": {"milestones": [{"start": a, "end": b, "difficulty": "high"}]},
            "transfer": {"observations": [{"distance": "near", "correct": True}]},
            "calibration": {"observations": [{"confidence": .25, "correct": False}]},
            "retrieval": {"observations": [{"delay_hours": 168, "correct": False}]},
            "scaffolding": {"observations": [{"level": "hint", "successful": True}]},
        }
        out = self.run_collect(rs)
        for key in rs:
            self.assertEqual(out[key]["status"], "reported", key)
        self.assertIn("0.062", json.dumps(out["calibration"]))
        self.assertEqual(self.run_collect({"transfer": {"observations": []}})["transfer"]["status"], "no_evidence")
        self.assertEqual(self.run_collect({"transfer": {"observations": [{}]*501}})["transfer"]["status"], "blocked")
        self.assertEqual(self.run_collect({"push-timeline": {"events": [{"at": "2025-01-01T01:00:00", "kind": "push"}]}})["push-timeline"]["status"], "blocked")
        self.assertEqual(self.run_collect({"transfer": {"observations": [{"distance": "near", "correct": 1}]}})["transfer"]["status"], "blocked")

    def test_unreadable_root_fail_closed(self):
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual({v["status"] for v in pe.collect(link).values()}, {"blocked"})


if __name__ == "__main__":
    unittest.main()
