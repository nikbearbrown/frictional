"""Synthetic projects only. Unit tests use a fake runtime; integration tests need Docker or Podman."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import container_run as cr

RUNTIME = cr.find_runtime()


def good_spec(**changes):
    spec = {"image": "python:3.12-slim",
            "setup": [{"cmd": "python -m pip install -r requirements.txt", "source": "README.md:12"}],
            "run": [{"cmd": "python app.py --demo", "source": "README.md:20"}],
            "test": [{"cmd": "python -m pytest -q", "source": "README.md:31"}],
            "serve": {"cmd": "python app.py", "port": 8000, "path": "/", "source": "README.md:25"},
            "timeout_seconds": 600, "network": "all"}
    spec.update(changes)
    return spec


class SpecTests(unittest.TestCase):
    def test_good_spec_is_normalized(self):
        spec = cr.check_spec(good_spec())
        self.assertEqual(spec["network"], "all")
        self.assertEqual(spec["serve"]["path"], "/")
        minimal = cr.check_spec({"image": "node:22-slim", "test": [{"cmd": "npm test", "source": "README.md:3"}]})
        self.assertEqual((minimal["setup"], minimal["run"], minimal["serve"]), ([], [], None))
        self.assertEqual((minimal["timeout_seconds"], minimal["network"]), (600, "setup"))
        digest = "python:3.12-slim@sha256:" + "a" * 64
        self.assertEqual(cr.check_spec(good_spec(image=digest))["image"], digest)
        self.assertEqual(cr.check_spec(good_spec(image="ghcr.io/org/tool"))["image"], "ghcr.io/org/tool")

    def test_bad_specs_fail_closed(self):
        cmd = {"cmd": "echo hi", "source": "README.md:1"}
        bad = [
            [], {"setup": [cmd]}, good_spec(extra=1), good_spec(image="Python:3"),
            good_spec(image="-v /:/host"), good_spec(image="python;rm"), good_spec(image="localhost:5000/x"),
            good_spec(image="python@sha256:abc"), good_spec(setup=cmd), good_spec(setup=[cmd] * 21),
            good_spec(setup=[{"cmd": "x" * 2001, "source": "README.md:1"}]),
            good_spec(setup=[{"cmd": "a\0b", "source": "README.md:1"}]),
            good_spec(setup=[{"cmd": "  ", "source": "README.md:1"}]),
            good_spec(setup=[{"cmd": "echo"}]), good_spec(setup=[{"cmd": "echo", "source": "s" * 201}]),
            good_spec(setup=[{"cmd": "echo", "source": "line\nbreak"}]),
            good_spec(setup=[{"cmd": "echo", "source": "README.md:1", "why": "x"}]),
            good_spec(timeout_seconds=0), good_spec(timeout_seconds=3601), good_spec(timeout_seconds=True),
            good_spec(timeout_seconds="600"), good_spec(timeout_seconds=1.5), good_spec(network="host"),
            good_spec(network="setup"), good_spec(network="none"),
            good_spec(serve={"cmd": "x", "port": 0, "source": "README.md:1"}),
            good_spec(serve={"cmd": "x", "port": True, "source": "README.md:1"}),
            good_spec(serve={"cmd": "x", "port": 80, "path": "no-slash", "source": "README.md:1"}),
            good_spec(serve={"cmd": "x", "port": 80, "path": "/a b", "source": "README.md:1"}),
            good_spec(serve={"cmd": "x", "port": 80}),
            {"image": "python:3.12-slim"},
        ]
        for value in bad:
            with self.subTest(value=str(value)[:80]):
                with self.assertRaises(ValueError):
                    cr.check_spec(value)

    def test_load_spec_bounds(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            good = base / "spec.json"
            good.write_text(json.dumps(good_spec()))
            self.assertEqual(cr.load_spec(good)["image"], "python:3.12-slim")
            cases = {
                "dup.json": '{"image": "python:3.12-slim", "image": "node:22-slim", "run": []}',
                "nan.json": '{"image": "python:3.12-slim", "timeout_seconds": NaN}',
                "bad.json": "{not json",
                "list.json": "[]",
                "big.json": '{"image": "python:3.12-slim", "x": "' + "a" * 70000 + '"}',
            }
            for name, text in cases.items():
                (base / name).write_text(text)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    cr.load_spec(base / name)
            link = base / "link.json"
            link.symlink_to(good)
            with self.assertRaises(ValueError):
                cr.load_spec(link)
            with self.assertRaises(ValueError):
                cr.load_spec(base / "missing.json")


class DefaultSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return {name: len(text) for name, text in files.items()}

    def test_requirements_with_pytest(self):
        files = self.make({"requirements.txt": "numpy==1.26\npytest>=8\n", "tests/test_core.py": "x"})
        spec = cr.default_spec(self.root, files)
        self.assertEqual(spec["image"], "python:3.12-slim")
        self.assertEqual(spec["setup"][0]["cmd"], "python -m pip install --no-cache-dir -r requirements.txt")
        self.assertEqual(spec["test"][0]["cmd"], "python -m pytest -q")
        self.assertEqual((spec["run"], spec["serve"]), ([], None))
        self.assertTrue(all(c["source"].startswith("default: ") for c in spec["setup"] + spec["test"]))

    def test_requirements_include_and_unittest_fallbacks(self):
        files = self.make({"requirements.txt": "-r requirements-dev.txt\n", "requirements-dev.txt": "pytest-cov\n",
                           "test_x.py": "x"})
        self.assertEqual(cr.default_spec(self.root, files)["test"][0]["cmd"], "python -m pytest -q")
        (self.root / "requirements.txt").write_text("requests\n")
        files = {"requirements.txt": 9, "test_x.py": 1}
        self.assertEqual(cr.default_spec(self.root, files)["test"][0]["cmd"], "python -m unittest discover -v")
        files = {"requirements.txt": 9, "tests/test_x.py": 1}
        self.assertEqual(cr.default_spec(self.root, files)["test"][0]["cmd"],
                         "python -m unittest discover -s tests -v")
        self.assertNotIn("test", {k for k, v in cr.default_spec(self.root, {"requirements.txt": 9}).items() if v})

    def test_pyproject_only(self):
        files = self.make({"pyproject.toml": '[project]\nname = "x"\ndependencies = ["pytest>=7"]\n'
                                             '[tool.pytest.ini_options]\naddopts = "-q"\n',
                           "pkg/test_mod.py": "x"})
        spec = cr.default_spec(self.root, files)
        self.assertEqual(spec["setup"][0], {"cmd": "python -m pip install --no-cache-dir .",
                                            "source": "default: pyproject.toml"})
        self.assertEqual(spec["test"][0]["cmd"], "python -m pytest -q")
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\n[tool.pytest.ini_options]\n')
        self.assertEqual(cr.default_spec(self.root, files)["test"][0]["cmd"], "python -m unittest discover -v")

    def test_node(self):
        files = self.make({"package.json": json.dumps({"scripts": {"test": "jest"}}), "package-lock.json": "{}"})
        spec = cr.default_spec(self.root, files)
        self.assertEqual((spec["image"], spec["setup"][0]["cmd"], spec["test"][0]["cmd"]),
                         ("node:22-slim", "npm ci", "npm test"))
        placeholder = 'echo "Error: no test specified" && exit 1'
        (self.root / "package.json").write_text(json.dumps({"scripts": {"test": placeholder}}))
        spec = cr.default_spec(self.root, {"package.json": 1})
        self.assertEqual((spec["setup"][0]["cmd"], spec["test"]), ("npm install", []))

    def test_nothing_recognisable(self):
        files = self.make({"main.py": "print(1)", "src/requirements.txt": "x", "test_main.py": "x"})
        self.assertIsNone(cr.default_spec(self.root, files))


class MaskAndCategoryTests(unittest.TestCase):
    def test_masking(self):
        secrets = {
            "OPENAI": "key sk-proj-abcdefghijklmnopqrstuvwx1234",
            "GH": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "AWS": "AKIAABCDEFGHIJKLMNOP",
            "ASSIGN": "DB_PASSWORD=hunter2hunter2",
            "AWS_SECRET": "aws_secret_access_key = wJalrXUtnFEMIK7MDENG",
            "URL": "postgres://user:pa55word@db.example.invalid/x",
            "BEARER": "Authorization: Bearer abcdefghijklmnop",
            "JSON": '"api_key": "abcdef123456"',
        }
        for name, text in secrets.items():
            masked = cr.mask_secrets(text)
            with self.subTest(name=name):
                self.assertIn("[masked]", masked)
        self.assertNotIn("hunter2", cr.mask_secrets(secrets["ASSIGN"]))
        self.assertNotIn("pa55word", cr.mask_secrets(secrets["URL"]))
        self.assertNotIn("abcdef123456", cr.mask_secrets(secrets["JSON"]))
        key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nAAAA\n-----END RSA PRIVATE KEY-----\nafter"
        self.assertEqual(cr.mask_secrets(key).split("\n"), ["[masked]"] * 4 + ["after"])
        plain = "Collecting tokenizers==0.15\nRan 3 tests in 0.1s\nauthor: someone"
        self.assertEqual(cr.mask_secrets(plain), plain)

    def test_categories(self):
        cases = {
            "  File x, line 2\nSyntaxError: invalid syntax\nFAILED": "syntax",
            "IndentationError: unexpected indent": "syntax",
            "ModuleNotFoundError: No module named 'numpy'\nFAILED (errors=1)": "dependency",
            "Error: Cannot find module 'express'": "dependency",
            "sh: 1: pytest: not found": "dependency",
            "AssertionError: 3 != 4\nFAILED (failures=1)": "assertion",
            "  2 failing": "assertion",
            "=== 1 failed, 2 passed in 0.1s ===": "assertion",
            "src/a.ts(3,1): error TS2322: Type": "build",
            "main.c:3: error: expected ';'": "build",
            "Killed": "other",
        }
        for text, category in cases.items():
            with self.subTest(text=text):
                self.assertEqual(cr.failure_category(text), category)
        self.assertEqual(cr.failure_category("AssertionError", timed_out=True), "timeout")


def receipt(**changes):
    """A hand-written receipt in the shape run_project writes."""
    def cmd(phase, n, code=0, category=None, source="README.md:12", order=None, timed_out=False):
        row = {"phase": phase, "index": n, "source": source, "cmd": "SECRET_COMMAND_TEXT", "exit_code": code,
               "timed_out": timed_out, "seconds": 1.25, "t_start": 0.0, "t_end": 1.25,
               "log": f"run/logs/{phase}-{order}-{n}.log" if order else f"run/logs/{phase}-{n}.log",
               "log_sha256": "0" * 64, "output_truncated": False}
        if order:
            row["order"] = order
        if code != 0:
            row["failure_category"] = category or "other"
        return row
    base = {
        "runner": "frictional", "receipt_version": 1, "runtime": {"name": "docker", "version": "29.5.2"},
        "image": "python:3.12-slim", "image_digest": "python@sha256:" + "a" * 64, "image_id": None,
        "image_platform": "linux/arm64", "image_pulled": False, "revision": "c" * 40,
        "source_tree": "working-tree copy", "started_at": "2026-11-23T00:00:00Z",
        "finished_at": "2026-11-23T00:01:00Z", "network": "setup", "network_during_setup": "on",
        "network_during_run": "off", "network_check": "verified-off", "isolated": True,
        "environment": "posix-container", "timing": "streamed",
        "container": {"name": "frictional-x", "label": "frictional=1", "removed": True},
        "limits": {"memory": "2g", "cpus": 2, "pids": 512, "timeout_seconds": 600},
        "copy": {"files": 3, "bytes": 10, "symlinks_skipped": 0, "cache_dirs_skipped": 0,
                 "credential_like_skipped": 0, "other_skipped": 0},
        "planned": {"setup": 1, "run": 1, "test": 1, "serve": 0, "history": 0},
        "commands": [cmd("setup", 1, source="default: requirements.txt"), cmd("run", 1, source="README.md:20"),
                     cmd("test", 1, 1, "assertion", source="README.md:31")],
        "phases": {"setup": "passed", "run": "passed", "test": "failed"},
        "history": [], "serve": None,
        "overall": {"setup": "passed", "run": "passed", "test": "failed", "serve": "not_run", "history": "not_run"},
        "transcript": {"path": "run/transcript.jsonl", "lines": 3, "dropped": 0, "sha256": "0" * 64},
        "error": None, "stopped": None, "notes": [],
    }
    base["_cmd"] = cmd
    base.update(changes)
    cmd = base.pop("_cmd")
    return base, cmd


class RunResultsTests(unittest.TestCase):
    def test_reported_mapping_has_no_raw_text(self):
        r, _ = receipt()
        out = cr.run_results(r)
        self.assertEqual(tuple(out), cr.RUN_DIMENSIONS)
        self.assertEqual({k: v["status"] for k, v in out.items()},
                         {"install-test": "reported", "run-check": "reported",
                          "test-history": "no_evidence", "serve-check": "no_evidence"})
        text = json.dumps(out)
        self.assertNotIn("SECRET_COMMAND_TEXT", text)
        install = out["install-test"]["facts"][0]
        self.assertIn("1/1 command(s) exited 0", install["observation"])
        self.assertIn("default: requirements.txt", install["observation"])
        self.assertIn(cr.ALT_DEFAULT, install["alternative_explanations"])
        self.assertIn(cr.ALT_CLEAN, install["alternative_explanations"])
        run_check = out["run-check"]["facts"]
        self.assertIn("Failure categories: assertion: 1", run_check[1]["observation"])
        self.assertEqual(run_check[2]["locator"], "run/logs/test-1.log")
        self.assertIn(cr.ALT_TESTS, run_check[1]["alternative_explanations"])

    def test_setup_failure_blocks_later_phases(self):
        r, cmd = receipt(commands=[], phases={"setup": "failed", "run": "not_run", "test": "not_run"},
                         stopped="setup-failed")
        r["commands"] = [cmd("setup", 1, 1, "dependency")]
        r["planned"].update(serve=1, history=2)
        out = cr.run_results(r)
        self.assertEqual(out["install-test"]["status"], "reported")
        self.assertIn("exited 1", out["install-test"]["facts"][1]["observation"])
        for dim in ("run-check", "test-history", "serve-check"):
            self.assertEqual(out[dim]["status"], "blocked", dim)
            self.assertIn("setup failed", out[dim]["facts"][0]["observation"])

    def test_history_transitions(self):
        r, cmd = receipt()
        r["planned"]["history"] = 4
        outcomes = [("failed", 1), ("passed", 0), ("failed", 1), ("passed", 0)]
        r["history"] = []
        for order, (outcome, code) in enumerate(outcomes, 1):
            h = {"order": order, "commit": f"{order:040x}", "outcome": outcome, "logs": []}
            if code:
                h["failure_category"] = "assertion" if order == 1 else "dependency"
            r["history"].append(h)
            r["commands"].append(cmd("history", 1, code, h.get("failure_category"), "README.md:31", order))
        fact = cr.run_results(r)["test-history"]["facts"][0]
        self.assertIn("4/4 supplied commit(s)", fact["observation"])
        self.assertIn("2 passed, 2 failed, 0 not run", fact["observation"])
        self.assertIn("fail to pass 2, pass to fail 1", fact["observation"])
        self.assertIn("assertion: 1, dependency: 1", fact["observation"])
        self.assertIn("Outcome runs in order: 1 fail, 1 pass, 1 fail, 1 pass", fact["observation"])
        self.assertIn(cr.ALT_HISTORY, fact["alternative_explanations"])

    def test_serve_and_errors(self):
        r, _ = receipt()
        r["planned"]["serve"] = 1
        r["serve"] = {"attempted": True, "source": "README.md:25", "port": 8000, "path": "/", "host_port": 40000,
                      "answered": True, "http_status": 200, "seconds_to_answer": 1.5, "held_seconds": 0,
                      "hold_interrupted": False, "state": "running", "exit_code": None,
                      "log": "run/logs/serve-1.log", "log_sha256": "0" * 64}
        fact = cr.run_results(r)["serve-check"]["facts"][0]
        self.assertIn("HTTP status 200", fact["observation"])
        self.assertEqual(fact["locator"], "run/logs/serve-1.log")
        r["serve"].update(answered=False, http_status=None, state="exited", exit_code=2)
        self.assertIn("exited with code 2", cr.run_results(r)["serve-check"]["facts"][0]["observation"])
        r["error"] = "image-unavailable"
        out = cr.run_results(r)
        self.assertEqual({v["status"] for v in out.values()}, {"blocked", "no_evidence"})
        self.assertEqual(out["test-history"]["status"], "no_evidence")

    def test_no_phases_and_malformed(self):
        r, _ = receipt(commands=[], phases={"setup": "not_run", "run": "not_run", "test": "not_run"})
        r["planned"] = {"setup": 0, "run": 0, "test": 0, "serve": 0, "history": 0}
        self.assertEqual({v["status"] for v in cr.run_results(r).values()}, {"no_evidence"})
        for broken in ({}, [], {"runner": "frictional"}, dict(receipt()[0], image="bad image")):
            out = cr.run_results(broken)
            self.assertEqual({v["status"] for v in out.values()}, {"blocked"})
        r, _ = receipt()
        r["commands"][0]["source"] = "raw README text\nwith a newline"
        self.assertEqual(cr.run_results(r)["install-test"]["status"], "blocked")


class FakeProc:
    def __init__(self, out=b"", err=b"", code=0, hang=False):
        self.stdout, self.stderr = io.BytesIO(out), io.BytesIO(err)
        self.code, self.hang, self.killed = code, hang, False
        self.returncode = None

    def poll(self):
        if self.hang and not self.killed:
            return None
        self.returncode = -9 if self.killed else self.code
        return self.returncode

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.poll()

    def kill(self):
        self.killed = True


class FakeRuntime:
    """Interprets runtime CLI argument lists; nothing is executed."""

    def __init__(self):
        self.calls, self.spawned = [], []

    def call(self, argv, timeout=None, stdin=None):
        self.calls.append(argv)
        action = argv[1]
        if action == "image":
            info = [{"RepoDigests": ["python@sha256:" + "a" * 64], "Id": "sha256:" + "b" * 64,
                     "Os": "linux", "Architecture": "arm64"}]
            return 0, json.dumps(info).encode(), b""
        if action == "info":
            return 0, b"4\n", b""
        if action == "exec" and "self $$" in argv[-1]:
            return 0, b"1\n7\n20\nself\n20\n", b""
        if action == "exec" and argv[-1] == "ls /sys/class/net":
            return 0, b"lo\n", b""
        if action == "inspect":
            return 0, b'{"bridge": {}}', b""
        if action == "port":
            return 0, b"127.0.0.1:40000\n", b""
        if action == "cp" and argv[2] == "-":
            stdin.read()
        return 0, b"", b""

    def spawn(self, argv):
        self.spawned.append(argv)
        cmd = argv[-1]
        if "FAILSETUP" in cmd:
            return FakeProc(b"ERROR: could not install\n", b"", 1)
        if "FAIL" in cmd:
            return FakeProc(b"Traceback\n", b"AssertionError: boom\nFAILED (failures=1)\n", 1)
        if "SECRET" in cmd:
            return FakeProc(b"token=abcd1234abcd sk-abcdefghijklmnop1234\n", b"", 0)
        if "SLEEP" in cmd or "SERVE" in cmd:
            return FakeProc(b"waiting\n", b"", 0, hang=True)
        return FakeProc(b"ok line\n", b"warn line\n", 0)


class FakeRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.out = base / "project", base / "out"
        self.root.mkdir()
        self.out.mkdir()
        (self.root / "app.py").write_text("print('hi')\n")
        (self.root / ".env").write_text("TOKEN=not-copied\n")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (self.root / "node_modules").mkdir()
        (self.root / "link.py").symlink_to(self.root / "app.py")
        self.fake = FakeRuntime()

    def run_fake(self, spec, **kwargs):
        with patch.object(cr, "_call", self.fake.call), patch.object(cr, "_spawn", self.fake.spawn), \
                patch.object(cr, "_probe", lambda url: 200), \
                patch.object(cr.shutil, "which", lambda name: "/fake/" + name), \
                patch("subprocess.Popen", side_effect=AssertionError("host execution attempted")), \
                patch("subprocess.run", side_effect=AssertionError("host execution attempted")):
            return cr.run_project(self.root, spec, self.out, runtime={"name": "docker", "version": "29.5.2"},
                                  **kwargs)

    def test_full_fake_run(self):
        spec = {"image": "python:3.12-slim",
                "setup": [{"cmd": "pip install SECRET", "source": "README.md:3"}],
                "run": [{"cmd": "SLEEP forever", "source": "README.md:5"}, {"cmd": "python app.py", "source": "README.md:6"}],
                "test": [{"cmd": "FAIL tests", "source": "README.md:9"}],
                "serve": {"cmd": "SERVE app", "port": 8000, "source": "README.md:12"},
                "timeout_seconds": 5, "network": "all"}
        archive = Path(self.temp.name) / "c1.tar"
        archive.write_bytes(b"tar")
        r = self.run_fake(spec, serve=True, history=[{"commit": "a" * 40, "tar": archive}], revision="b" * 40)
        run = self.out / "run"
        self.assertEqual(json.loads((run / "receipt.json").read_text()), r)
        self.assertEqual(r["overall"], {"setup": "passed", "run": "failed", "test": "failed",
                                        "serve": "passed", "history": "failed"})
        self.assertEqual(r["image_digest"], "python@sha256:" + "a" * 64)
        self.assertEqual(r["copy"]["credential_like_skipped"], 1)
        self.assertEqual((r["copy"]["symlinks_skipped"], r["copy"]["cache_dirs_skipped"]), (1, 1))
        self.assertTrue(r["container"]["removed"])
        sleep = [c for c in r["commands"] if c["phase"] == "run"][0]
        self.assertEqual((sleep["timed_out"], sleep["exit_code"], sleep["failure_category"]), (True, None, "timeout"))
        test = [c for c in r["commands"] if c["phase"] == "test"][0]
        self.assertEqual(test["failure_category"], "assertion")
        self.assertEqual(r["history"][0]["outcome"], "failed")
        self.assertTrue(r["serve"]["answered"])
        for c in r["commands"]:
            data = (self.out / c["log"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), c["log_sha256"])
        everything = "".join(p.read_text() for p in run.rglob("*") if p.is_file())
        self.assertNotIn("abcd1234abcd", everything)
        self.assertNotIn("sk-abcdefghijklmnop1234", everything)
        rows = [json.loads(line) for line in (run / "transcript.jsonl").read_text().splitlines()]
        self.assertTrue(rows and all(set(row) - {"order"} == {"t", "phase", "n", "stream", "line"} for row in rows))
        self.assertEqual({row["stream"] for row in rows}, {"stdout", "stderr"})
        # Container settings: isolation flags, no mounts, no socket, port only on localhost.
        create = next(c for c in self.fake.calls if c[1] == "create")
        for flag in ("--memory", "--cpus", "--pids-limit", "--security-opt", "--cap-drop", "--label", "--init"):
            self.assertIn(flag, create)
        self.assertEqual(create[create.index("--cap-drop") + 1], "ALL")
        self.assertIn("127.0.0.1::8000", create)
        flat = " ".join(" ".join(c) for c in self.fake.calls + self.fake.spawned)
        for forbidden in ("-v ", "--volume", "--mount", "docker.sock", "--privileged"):
            self.assertNotIn(forbidden, flat)
        self.assertTrue(all(argv[-3:-1] == ["sh", "-c"] for argv in self.fake.spawned))
        self.assertEqual(self.fake.calls[-1][1:3], ["rm", "-f"])
        statuses = {k: v["status"] for k, v in cr.run_results(r).items()}
        self.assertEqual(statuses, dict.fromkeys(cr.RUN_DIMENSIONS, "reported"))

    def test_fake_setup_failure_and_refusals(self):
        spec = {"image": "python:3.12-slim", "setup": [{"cmd": "FAILSETUP", "source": "README.md:3"},
                                                       {"cmd": "never", "source": "README.md:4"}],
                "run": [{"cmd": "python app.py", "source": "README.md:6"}]}
        r = self.run_fake(spec)
        self.assertEqual((r["phases"], r["stopped"]), ({"setup": "failed", "run": "not_run", "test": "not_run"},
                                                       "setup-failed"))
        self.assertEqual(len(r["commands"]), 1)
        self.assertFalse(any("disconnect" in c for c in self.fake.calls))
        self.assertEqual(cr.run_results(r)["run-check"]["status"], "blocked")
        with self.assertRaises(ValueError):  # run/ already exists
            self.run_fake(spec)
        inside = self.root / "out"
        inside.mkdir()
        with self.assertRaises(ValueError):
            cr.run_project(self.root, spec, inside, runtime={"name": "docker", "version": "1"})
        with self.assertRaises(ValueError):
            cr.run_project(self.root, spec, Path(self.temp.name), runtime={"name": "sh", "version": "1"})
        with self.assertRaises(ValueError):
            cr.run_project(self.root, spec, Path(self.temp.name), runtime={"name": "docker", "version": "1"},
                           serve=True)
        with self.assertRaises(ValueError):
            cr.run_project(self.root, spec, Path(self.temp.name), runtime={"name": "docker", "version": "1"},
                           history=[{"commit": "abc", "tar": "x"}])


@unittest.skipUnless(RUNTIME, "no Docker or Podman daemon answered")
class ContainerIntegrationTests(unittest.TestCase):
    """Synthetic projects run inside a real container; nothing from them runs on the host."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.out = base / "project", base / "out"
        self.root.mkdir()
        self.out.mkdir()

    def write(self, files):
        for name, text in files.items():
            (self.root / name).write_text(text)

    def assert_removed(self, r):
        self.assertTrue(r["container"]["removed"])
        listed = subprocess.run([RUNTIME["name"], "ps", "-a", "-q", "--filter", f"name={r['container']['name']}"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(listed.stdout.strip(), "")

    def test_install_run_test_serve(self):
        self.write({"requirements.txt": "# no dependencies\n", "app.py": "print('hello from app')\n",
                    "test_app.py": "import unittest\n\nclass T(unittest.TestCase):\n"
                                   "    def test_ok(self):\n        self.assertEqual(1 + 1, 2)\n"})
        spec = {"image": "python:3.12-slim",
                "setup": [{"cmd": "python -m pip install --no-cache-dir -r requirements.txt", "source": "README.md:3"}],
                "run": [{"cmd": "python app.py", "source": "README.md:6"}],
                "test": [{"cmd": "python -m unittest discover -v", "source": "README.md:9"}],
                "serve": {"cmd": "python -m http.server 8000", "port": 8000, "source": "README.md:12"},
                "timeout_seconds": 300, "network": "all"}
        r = cr.run_project(self.root, spec, self.out, runtime=RUNTIME, serve=True)
        self.assertEqual(r["overall"], {"setup": "passed", "run": "passed", "test": "passed",
                                        "serve": "passed", "history": "not_run"}, r.get("notes"))
        self.assertEqual(r["serve"]["http_status"], 200)
        rows = [json.loads(x) for x in (self.out / "run/transcript.jsonl").read_text().splitlines()]
        self.assertIn({"phase": "run", "line": "hello from app"},
                      [{"phase": x["phase"], "line": x["line"]} for x in rows])
        self.assertTrue(any(x["phase"] == "serve" for x in rows))
        times = [x["t"] for x in rows]
        self.assertEqual(times, sorted(times))
        self.assert_removed(r)
        statuses = {k: v["status"] for k, v in cr.run_results(r).items()}
        self.assertEqual(statuses, {"install-test": "reported", "run-check": "reported",
                                    "test-history": "no_evidence", "serve-check": "reported"})

    def test_network_off_timeout_and_copy_exclusions(self):
        self.write({"app.py": "print(1)\n", ".env": "API_TOKEN=do-not-copy\n"})
        (self.root / "__pycache__").mkdir()
        spec = {"image": "python:3.12-slim",
                "setup": [{"cmd": "python -c \"print('setup ok')\"", "source": "README.md:3"}],
                "run": [{"cmd": "sleep 60", "source": "README.md:5"},
                        {"cmd": "python -c \"import os; print(sorted(os.listdir('/sys/class/net')))\"",
                         "source": "README.md:6"},
                        {"cmd": "test ! -e .env && test ! -e __pycache__ && echo excluded", "source": "README.md:7"}],
                "timeout_seconds": 8, "network": "setup"}
        r = cr.run_project(self.root, spec, self.out, runtime=RUNTIME)
        self.assertEqual(r["network_check"], "verified-off")
        runs = [c for c in r["commands"] if c["phase"] == "run"]
        self.assertEqual((runs[0]["timed_out"], runs[0]["failure_category"]), (True, "timeout"))
        self.assertEqual([c["exit_code"] for c in runs[1:]], [0, 0])
        log = (self.out / runs[1]["log"]).read_text()
        self.assertIn("['lo']", log)
        self.assertIn("excluded", (self.out / runs[2]["log"]).read_text())
        self.assertEqual(r["copy"]["credential_like_skipped"], 1)
        self.assert_removed(r)

    def test_failing_setup(self):
        self.write({"requirements.txt": "./does-not-exist\n", "app.py": "print(1)\n"})
        spec = {"image": "python:3.12-slim",
                "setup": [{"cmd": "python -m pip install --no-cache-dir -r requirements.txt", "source": "README.md:3"}],
                "run": [{"cmd": "python app.py", "source": "README.md:6"}], "network": "none",
                "timeout_seconds": 120}
        r = cr.run_project(self.root, spec, self.out, runtime=RUNTIME)
        self.assertEqual((r["phases"]["setup"], r["phases"]["run"], r["stopped"]), ("failed", "not_run", "setup-failed"))
        self.assertNotEqual(r["commands"][0]["exit_code"], 0)
        out = cr.run_results(r)
        self.assertEqual((out["install-test"]["status"], out["run-check"]["status"]), ("reported", "blocked"))
        self.assert_removed(r)

    def test_history_fail_then_pass(self):
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

        def git(*args):
            return subprocess.run(["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                                   "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                                  cwd=repo, env=env, capture_output=True, text=True, check=True, timeout=60).stdout

        test = "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n" \
               "    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n"
        git("init", "-q")
        (repo / "test_calc.py").write_text(test)
        commits, tars = [], []
        for body in ("def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a + b\n"):
            (repo / "calc.py").write_text(body)
            git("add", "-A")
            git("commit", "-q", "-m", "synthetic")
            commits.append(git("rev-parse", "HEAD").strip())
            tars.append(Path(self.temp.name) / f"c{len(commits)}.tar")
            git("archive", "--format=tar", "-o", str(tars[-1]), commits[-1])
        for name in ("calc.py", "test_calc.py"):
            (self.root / name).write_text((repo / name).read_text())
        spec = {"image": "python:3.12-slim",
                "setup": [{"cmd": "python -c \"print('setup')\"", "source": "README.md:2"}],
                "test": [{"cmd": "python -m unittest discover -v", "source": "README.md:4"}],
                "network": "none", "timeout_seconds": 120}
        r = cr.run_project(self.root, spec, self.out, runtime=RUNTIME, revision=commits[-1],
                           history=[{"commit": c, "tar": t} for c, t in zip(commits, tars)])
        self.assertEqual([h["outcome"] for h in r["history"]], ["failed", "passed"])
        self.assertEqual(r["history"][0]["failure_category"], "assertion")
        self.assertEqual(r["phases"]["test"], "passed")
        fact = cr.run_results(r)["test-history"]["facts"][0]["observation"]
        self.assertIn("fail to pass 1, pass to fail 0", fact)
        self.assertIn("1 passed, 1 failed", fact)
        self.assert_removed(r)


if __name__ == "__main__":
    unittest.main()
