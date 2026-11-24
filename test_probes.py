"""Synthetic projects only; never student work.

Unit tests replace container_run.run_project with a stand-in that runs probe_harness.py on the
synthetic, test-written copy; the real-container tests need Docker or Podman.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import container_run as cr
import frictional_cli
import probe_harness
import probes
import process_evidence as pe

HERE = Path(__file__).resolve().parent
RUNTIME = cr.find_runtime()

CALC = '''"""Small synthetic helpers for probe tests."""
import functools
import random


def clamp(value, low, high):
    """Keep value between low and high."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def band(score):
    """A letter band for a score out of 100."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    return "D"


def tally(words):
    """Counts of each word, ignoring case and empty strings."""
    counts = {}
    for word in words:
        key = word.lower()
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def shout(text):
    return text.upper()


def noisy(values):
    total = 0
    for v in values:
        if v > 0:
            total += v
    print(total)
    return total


def pick(values):
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return random.choice(values)


def kept(values):
    """Same as the starter."""
    out = []
    for v in values:
        if v:
            out.append(v)
    return out


@functools.lru_cache(maxsize=None)
def cached(n):
    if n < 2:
        return n
    return cached(n - 1) + cached(n - 2)


def evens(values):
    for v in values:
        if v % 2 == 0:
            yield v
'''

STARTER = '''"""Small synthetic helpers for probe tests."""
import functools
import random


def clamp(value, low, high):
    """Keep value between low and high."""
    raise NotImplementedError


def band(score):
    """A letter band for a score out of 100."""
    raise NotImplementedError


def tally(words):
    """Counts of each word, ignoring case and empty strings."""
    raise NotImplementedError


def kept(values):
    """Same as the starter."""
    out = []
    for v in values:
        if v:
            out.append(v)
    return out
'''

TESTS = '''import unittest
from calc import band, clamp, tally


class T(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_band(self):
        self.assertEqual(band(85), "B")

    def test_tally(self):
        self.assertEqual(tally(["a", "A", "b"]), {"a": 2, "b": 1})
        self.assertEqual(tally(["x", "", "X", "y"]), {"x": 2, "y": 1})
'''

README = '''# Calc

```pycon
>>> band(72)
'C'
```
'''

REFERENCE = '''from calc import band


def band_plus(score):
    """Like band, but 95 and above is A+."""
    if score >= 95:
        return "A+"
    return band(score)
'''


def write_project(root: Path, starter: Path):
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(CALC)
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_calc.py").write_text(TESTS)
    (root / "README.md").write_text(README)
    (root / "requirements.txt").write_text("# no dependencies\n")
    starter.mkdir()
    (starter / "calc.py").write_text(STARTER)


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(str(path.relative_to(root)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


def fake_run_project(root, spec, out_dir, *, runtime, revision=None, **_):
    """Stand-in for the container: run the harness on the synthetic copy and write a receipt and logs."""
    spec = cr.check_spec(spec)
    run_dir = Path(out_dir) / "run"
    (run_dir / "logs").mkdir(parents=True)
    work = Path(out_dir).parent / f".fake-work-{os.getpid()}-{len(list(Path(out_dir).parent.iterdir()))}"
    shutil.copytree(root, work)
    commands = []
    try:
        for n, command in enumerate(spec["run"], 1):
            seed = command["cmd"].split("PYTHONHASHSEED=")[1].split()[0]
            calls = command["cmd"].split()[-1]
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, ".frictional-probe/probe_harness.py", calls], cwd=work,
                                  env=env, capture_output=True, text=True, timeout=120)
            body = "\n".join(cr.mask_secrets(line) for line in proc.stdout.splitlines()) + "\n"
            rel = f"run/logs/run-{n}.log"
            text = f"# frictional run log\n# phase: run  command: {n}  source: {command['source']}\n$ {command['cmd']}\n{body}# exit code: {proc.returncode}\n"
            (Path(out_dir) / rel).write_text(text)
            commands.append({"phase": "run", "index": n, "log": rel,
                             "log_sha256": hashlib.sha256(text.encode()).hexdigest(), "exit_code": proc.returncode})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    receipt = {"runner": "fake", "commands": commands, "phases": {"setup": "passed"}}
    (run_dir / "receipt.json").write_text(json.dumps(receipt))
    return receipt


FAKE_RUNTIME = {"name": "docker", "version": "fake"}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.starter, self.out = base / "project", base / "starter", base / "out"
        self.root.mkdir()
        write_project(self.root, self.starter)

    def make(self, **kwargs):
        options = dict(run=True, runtime=FAKE_RUNTIME, deadline="2027-02-14T23:59:00-05:00", echo=lambda *_: None)
        options.update(kwargs)
        with patch.object(cr, "run_project", fake_run_project):
            return probes.make(self.root, self.starter, self.out, **options)


class SelectionTests(Base):
    def test_rules_and_exclusions(self):
        files, _ = pe.inventory(self.root)
        candidates, stats = probes.select_static(self.root, self.starter, files, 1)
        self.assertEqual(sorted(c["name"] for c in candidates), ["band", "clamp", "tally"])
        excluded = stats["excluded"]
        self.assertEqual((excluded["unchanged"], excluded["io-or-clock"], excluded["trivial"],
                          excluded["decorated"], excluded["async-or-generator"]), (1, 2, 1, 1, 1))
        self.assertEqual(stats["test-files"], 2)

    def test_inputs_never_repeat_the_visible_literals(self):
        files, _ = pe.inventory(self.root)
        candidates, _ = probes.select_static(self.root, self.starter, files, 1)
        clamp = next(c for c in candidates if c["name"] == "clamp")
        shown = {"(5, 0, 10)", "(-3, 0, 10)"}
        self.assertFalse(shown & {i["args"] for i in clamp["inputs"]})
        self.assertLessEqual(len(clamp["inputs"]), probes.MAX_INPUTS)
        band = next(c for c in candidates if c["name"] == "band")
        self.assertTrue(any("README.md:4" in i["origin"] for i in band["inputs"]))
        again, _ = probes.select_static(self.root, self.starter, files, 1)
        self.assertEqual([c["inputs"] for c in candidates], [c["inputs"] for c in again])
        other, _ = probes.select_static(self.root, self.starter, files, 2)
        self.assertEqual({c["name"] for c in other}, {c["name"] for c in candidates})

    def test_nominated_and_no_starter_match(self):
        files, _ = pe.inventory(self.root)
        candidates, stats = probes.select_static(self.root, self.starter, files, 1, nominated=["band", "calc.py:tally"])
        self.assertEqual(sorted(c["name"] for c in candidates), ["band", "tally"])
        self.assertEqual(stats["excluded"]["not-nominated"], 1)

    def test_python_only(self):
        with TemporaryDirectory() as temp:
            (Path(temp) / "app.js").write_text("console.log(1)\n")
            files, _ = pe.inventory(Path(temp))
            with self.assertRaisesRegex(probes.ProbeError, "Python only"):
                probes.select_static(Path(temp), self.starter, files, 1)

    def test_variants_change_one_token_and_parse(self):
        files, _ = pe.inventory(self.root)
        candidates, _ = probes.select_static(self.root, self.starter, files, 1)
        clamp = next(c for c in candidates if c["name"] == "clamp")
        found = probes.variants(clamp)
        self.assertTrue(found)
        self.assertEqual(found[0]["operator"], "comparison-boundary")
        original = CALC.splitlines()
        for var in found:
            changed = var["text"].splitlines()
            diff = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
            self.assertEqual(len(diff), 1)
            self.assertEqual(diff[0] + 1, var["line"])
        tally = next(c for c in candidates if c["name"] == "tally")
        self.assertIn("constant-plus-one", {v["operator"] for v in probes.variants(tally)})
        self.assertNotIn("recursion", tally["features"])
        self.assertIn("counter", tally["features"])
        tree = probes.ast.parse("def most_common(t):\n    return Counter(t).most_common(1)\n"
                                "def fact(n):\n    return 1 if n < 2 else n * fact(n - 1)\n")
        self.assertNotIn("recursion", probes.features(tree.body[0]))
        self.assertIn("recursion", probes.features(tree.body[1]))


class HarnessTests(unittest.TestCase):
    """The harness runs here only on a synthetic module written by the test."""

    def run_harness(self, module_text, calls):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "m.py").write_text(module_text)
            (root / ".frictional-probe").mkdir()
            shutil.copyfile(HERE / "probe_harness.py", root / ".frictional-probe" / "probe_harness.py")
            nonce = "ab" * 16
            (root / ".frictional-probe" / "c.json").write_text(json.dumps({"nonce": nonce, "calls": calls}))
            proc = subprocess.run([sys.executable, ".frictional-probe/probe_harness.py", ".frictional-probe/c.json"],
                                  cwd=root, capture_output=True, text=True, timeout=60)
            self.assertFalse((root / ".frictional-probe" / "c.json").exists())
            return proc.stdout, nonce

    def test_outcomes(self):
        module = ("print('noise at import')\n"
                  "def ok(x):\n    print('noise in call')\n    return {'k': [x, (1, 2)]}\n"
                  "def bad(x):\n    raise KeyError(x)\n"
                  "def obj(x):\n    return object()\n"
                  "def long(x):\n    return 'y' * 500\n"
                  "def spin(x):\n    while True:\n        pass\n")
        call = lambda cid, fn, args="(1,)": {"id": cid, "module": "m.py", "function": fn, "args": args, "kwargs": "{}"}
        calls = [call("c1", "ok"), call("c2", "bad"), call("c3", "obj"), call("c4", "long"),
                 call("c5", "nope"), call("c6", "ok", "(open,)"), call("c7", "spin"), call("c8", "spin")]
        stdout, nonce = self.run_harness(module, calls)
        self.assertNotIn("noise", stdout)
        results, done, problems = probes.parse_log(stdout, nonce, {f"c{i}" for i in range(1, 9)})
        self.assertTrue(done)
        self.assertEqual(problems, [])
        self.assertEqual(results["c1"], {"status": "value", "repr": "{'k': [1, (1, 2)]}"})
        self.assertEqual(results["c2"], {"status": "raises", "type": "KeyError"})
        self.assertEqual([results[c]["reason"] for c in ("c3", "c4", "c5", "c6", "c7", "c8")],
                         ["not-plain", "too-long", "missing", "arguments", "timeout", "skipped"])

    def test_package_names(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pkg" / "sub").mkdir(parents=True)
            (root / "pkg" / "__init__.py").write_text("")
            (root / "pkg" / "sub" / "__init__.py").write_text("")
            self.assertEqual(probe_harness.package_name(str(root), "pkg/sub/m.py"), (str(root), "pkg.sub.m"))
            self.assertEqual(probe_harness.package_name(str(root), "m.py"), (str(root), "m"))
            (root / "lib").mkdir()
            self.assertEqual(probe_harness.package_name(str(root), "lib/m.py"), (str(root / "lib"), "m"))


class ParseLogTests(unittest.TestCase):
    NONCE = "cd" * 16

    def line(self, obj, nonce=None):
        return f"{probes.MARK} {nonce or self.NONCE} {json.dumps(obj)}"

    def test_forgery_masking_and_shape(self):
        good = {"id": "c1", "status": "value", "repr": "3", "sha256": hashlib.sha256(b"3").hexdigest()}
        done = {"done": 1}
        results, finished, problems = probes.parse_log("\n".join([self.line(good), self.line(done)]),
                                                       self.NONCE, {"c1"})
        self.assertEqual((results, finished, problems), ({"c1": {"status": "value", "repr": "3"}}, True, []))
        for text in (self.line(good, "ef" * 16), self.line(good) + "\n" + self.line(good),
                     self.line(dict(good, id="c9")), self.line(dict(good, extra=1)),
                     self.line({"id": "c1", "status": "raises", "type": "not a name"})):
            with self.subTest(text=text[:60]):
                self.assertTrue(probes.parse_log(text, self.NONCE, {"c1"})[2])
        masked = dict(good, repr="token=[masked]")
        results, _, problems = probes.parse_log(self.line(masked), self.NONCE, {"c1"})
        self.assertEqual((results, problems), ({}, ["masked"]))


class FlowTests(Base):
    def test_make_writes_outside_the_project(self):
        before = tree_digest(self.root)
        probes_dir = self.make()
        self.assertEqual(tree_digest(self.root), before)
        spec = json.loads((probes_dir / "probe-spec.json").read_text())
        self.assertEqual(spec["status"], "draft")
        names = sorted(f["name"] for f in spec["functions"])
        self.assertEqual(names, ["band", "clamp", "tally"])
        kinds = [i["kind"] for i in spec["items"]]
        self.assertEqual(kinds.count("predict"), 6)
        self.assertEqual(kinds.count("bug"), 1)
        bug = next(i for i in spec["items"] if i["kind"] == "bug")
        self.assertEqual(spec["functions"][[f["id"] for f in spec["functions"]].index(bug["function"])]["name"], "tally")
        self.assertNotEqual(bug["witness"]["original"], bug["witness"]["variant"])
        self.assertTrue((probes_dir / bug["variant"]).is_file())
        for item in spec["items"]:
            if item["kind"] == "predict":
                self.assertIn(item["key"]["status"], ("value", "raises"))
        part1 = (probes_dir / "student-sheet-A-part1.md").read_text()
        part2 = (probes_dir / "student-sheet-A-part2.md").read_text()
        self.assertIn("def clamp(value, low, high):", part1)
        self.assertNotIn("if value < low", part1)
        self.assertNotIn("counts.get(key", part1)
        self.assertNotIn("bug-1", part1)
        self.assertIn(probes.show(bug["witness"]["variant"]), part2)
        self.assertNotIn(bug["hints"]["partial"], part2)
        self.assertEqual((probes_dir / "student-sheet-B.md").read_text().count("## Item"), 3)
        self.assertIn("Keep this file private", (probes_dir / "instructor-key.md").read_text())
        self.assertEqual(probes.verify_keys(spec, probes_dir), [])
        with self.assertRaises(probes.ProbeError):
            self.make()

    def test_unkeyed_without_run_or_runtime(self):
        probes_dir = self.make(run=False)
        spec = json.loads((probes_dir / "probe-spec.json").read_text())
        self.assertEqual((spec["status"], spec["items"]), ("draft-unkeyed", []))
        self.assertEqual(len(spec["candidates_preview"]), 3)
        with self.assertRaisesRegex(probes.ProbeError, "keyed draft"):
            probes.freeze(self.out, echo=lambda *_: None)

    def test_setup_failure_gives_no_keys(self):
        def failed(root, spec, out_dir, **_):
            (Path(out_dir) / "run").mkdir()
            receipt = {"commands": [], "stopped": "setup-failed"}
            (Path(out_dir) / "run" / "receipt.json").write_text(json.dumps(receipt))
            return receipt
        with patch.object(cr, "run_project", failed):
            probes_dir = probes.make(self.root, self.starter, self.out, run=True, runtime=FAKE_RUNTIME,
                                     echo=lambda *_: None)
        spec = json.loads((probes_dir / "probe-spec.json").read_text())
        self.assertEqual(spec["status"], "draft-unkeyed")
        self.assertTrue(any("setup-failed" in n for n in spec["notes"]))

    def fill(self, probes_dir):
        spec_path = probes_dir / "probe-spec.json"
        spec = json.loads(spec_path.read_text())
        for item in spec["items"]:
            item["difficulty"] = "medium"
            if item["kind"].startswith("transfer"):
                item["text"] = "Change band so that scores of 95 and above get the band A+."
                item["text_origin"] = "instructor"
                item["reference"].update(path="references/near_1.py", function="band_plus",
                                         calls=[{"args": "(97,)", "expect": "'A+'"}, {"args": "(50,)"}])
            if item["kind"] == "transfer-far":
                item["barnett_ceci"] = "knowledge-domain"
        (probes_dir / "references").mkdir()
        (probes_dir / "references" / "near_1.py").write_text(REFERENCE)
        spec_path.write_text(json.dumps(spec, indent=2))
        return spec_path

    def frozen(self):
        probes_dir = self.make()
        with self.assertRaisesRegex(probes.ProbeError, "difficulty"):
            probes.freeze(self.out, echo=lambda *_: None)
        spec_path = self.fill(probes_dir)
        with self.assertRaisesRegex(probes.ProbeError, "probe references"):
            probes.freeze(self.out, echo=lambda *_: None)
        with patch.object(cr, "run_project", fake_run_project):
            summary = probes.references(self.out, self.root, runtime=FAKE_RUNTIME, echo=lambda *_: None)
        self.assertEqual(summary, {"near-1": True, "far-1": True})
        digest = probes.freeze(self.out, echo=lambda *_: None)
        return probes_dir, spec_path, digest

    def test_freeze_checks_and_tamper(self):
        probes_dir, spec_path, digest = self.frozen()
        spec = probes.load_spec(spec_path)
        self.assertEqual(spec["frozen"]["sha256"], digest)
        self.assertTrue((probes_dir / "receipt-template-A.json").is_file())
        self.assertIn("scores of 95", (probes_dir / "student-sheet-A-part2.md").read_text())
        with self.assertRaisesRegex(probes.ProbeError, "already frozen"):
            probes.freeze(self.out, echo=lambda *_: None)
        spec["items"][0]["difficulty"] = "high"
        spec_path.write_text(json.dumps(spec))
        with self.assertRaisesRegex(probes.ProbeError, "changed since it was frozen"):
            probes.load_spec(spec_path)

    def test_hand_edited_key_is_refused(self):
        probes_dir = self.make()
        spec_path = self.fill(probes_dir)
        with patch.object(cr, "run_project", fake_run_project):
            probes.references(self.out, self.root, runtime=FAKE_RUNTIME, echo=lambda *_: None)
        spec = json.loads(spec_path.read_text())
        first = next(i for i in spec["items"] if i["kind"] == "predict")
        first["key"] = {"status": "value", "repr": "'invented'"}
        spec_path.write_text(json.dumps(spec))
        with self.assertRaisesRegex(probes.ProbeError, "does not match the container logs"):
            probes.freeze(self.out, echo=lambda *_: None)

    def test_agent_draft_needs_approval(self):
        probes_dir = self.make()
        spec_path = self.fill(probes_dir)
        spec = json.loads(spec_path.read_text())
        for item in spec["items"]:
            if item["kind"] == "transfer-near":
                item["text_origin"] = "agent-draft:claude-opus-5-5"
        spec_path.write_text(json.dumps(spec))
        problems = probes.check_items(spec, probes_dir, final=True)
        self.assertTrue(any("approved_by_instructor" in p for p in problems))

    def receipt(self, spec, form, started, **overrides):
        rows = []
        for item in spec["items"]:
            if item["form"] != form:
                continue
            row = {"item": item["id"], "attempted": True, "confidence": 0.75}
            if item["kind"] == "bug":
                row["solved_at"] = "partial"
            else:
                row["correct"] = item["kind"] != "transfer-far"
            rows.append(row)
        receipt = {"frictional_probe_receipt": 1, "spec_sha256": spec["frozen"]["sha256"],
                   "session": {"kind": "initial" if form == "A" else "reprobe", "form": form,
                               "started_at": started, "conditions": {"ai": False, "notes": False, "proctored": True}},
                   "items": rows}
        receipt.update(overrides)
        return receipt

    def test_receipts_to_packets(self):
        probes_dir, spec_path, _ = self.frozen()
        spec = probes.load_spec(spec_path)
        first = self.receipt(spec, "A", "2027-02-18T10:00:00-05:00")
        later = self.receipt(spec, "B", "2027-03-25T10:00:00-04:00")
        first["items"][0].update(started_at="2027-02-18T10:01:00-05:00", ended_at="2027-02-18T10:03:00-05:00")
        packets, notes = probes.receipts_to_packets(spec, [first, later])
        supported = hasattr(pe, "PACKET_OPTIONS")
        extra = {"scaffolding", "temporal-engagement"} if supported else set()
        self.assertEqual(set(packets), {"calibration", "transfer", "retrieval"} | extra)
        self.assertEqual(len(packets["calibration"]["observations"]), len(first["items"]) + len(later["items"]))
        bug_row = [r for r in packets["calibration"]["observations"]]
        self.assertIn({"confidence": 0.75, "correct": False}, bug_row)
        self.assertEqual(sorted(r["distance"] for r in packets["transfer"]["observations"]), ["far", "near"])
        delays = sorted({r["delay_hours"] for r in packets["retrieval"]["observations"]})
        self.assertEqual(len(delays), 2)
        self.assertGreater(delays[1], 168)
        self.assertEqual(any(n.startswith("scaffolding") for n in notes), not supported)
        self.assertEqual(any(n.startswith("temporal-engagement") for n in notes), not supported)
        for name, value in packets.items():
            self.assertEqual(pe.structured(name, value)["status"], "reported")

    def test_ladder_rows_when_the_adapter_supports_them(self):
        probes_dir, spec_path, _ = self.frozen()
        spec = probes.load_spec(spec_path)
        seen = {}
        options = {"scaffolding": {"design": ("ladder",)}, "temporal-engagement": {"grain": ("item",)}}
        original = pe.structured

        def structured(name, value, head=None):
            seen[name] = value
            if name in ("scaffolding", "temporal-engagement"):
                return {"status": "reported", "facts": []}
            return original(name, value, head)
        for solved, rows in (("none", [("none", True)]), ("partial", [("none", False), ("hint", True)]),
                             ("full", [("none", False), ("hint", False), ("worked", True)]),
                             ("unsolved", [("none", False), ("hint", False), ("worked", False)])):
            receipt = self.receipt(spec, "A", "2027-02-18T10:00:00-05:00")
            for row in receipt["items"]:
                if row["item"] == "bug-1":
                    row.update(solved_at=solved, started_at="2027-02-18T10:20:00-05:00",
                               ended_at="2027-02-18T10:31:00-05:00")
            with patch.object(pe, "PACKET_OPTIONS", options, create=True), patch.object(pe, "structured", structured):
                packets, _ = probes.receipts_to_packets(spec, [receipt])
            self.assertEqual(packets["scaffolding"]["design"], "ladder")
            self.assertEqual([(r["level"], r["successful"]) for r in packets["scaffolding"]["observations"]], rows)
            self.assertEqual(packets["temporal-engagement"]["grain"], "item")
            self.assertEqual(packets["temporal-engagement"]["milestones"][0]["difficulty"], "medium")

    def test_receipt_rejections(self):
        probes_dir, spec_path, _ = self.frozen()
        spec = probes.load_spec(spec_path)
        good = self.receipt(spec, "A", "2027-02-18T10:00:00-05:00")

        def variant(change):
            r = json.loads(json.dumps(good))
            change(r)
            return r
        bad = [
            variant(lambda r: r.update(extra=1)),
            variant(lambda r: r.update(spec_sha256="0" * 64)),
            variant(lambda r: r["session"].update(form="B")),
            variant(lambda r: r["session"].update(started_at="2027-02-18T10:00:00")),
            variant(lambda r: r["session"]["conditions"].update(ai=True)),
            variant(lambda r: r["items"].pop()),
            variant(lambda r: r["items"][0].update(confidence=0.6)),
            variant(lambda r: r["items"][0].update(name="Ada")),
            variant(lambda r: r["items"][0].update(solved_at="none")),
            variant(lambda r: next(i for i in r["items"] if i["item"] == "bug-1").update(correct=True)),
            variant(lambda r: r["items"][0].update(started_at="2027-02-18T10:01:00-05:00")),
            variant(lambda r: (r["items"][0].update(started_at="2027-02-18T10:01:00-05:00", ended_at="2027-02-18T10:05:00-05:00"),
                               r["items"][1].update(started_at="2027-02-18T10:04:00-05:00", ended_at="2027-02-18T10:06:00-05:00"))),
            variant(lambda r: r["items"][0].update(attempted=False)),
        ]
        for receipt in bad:
            with self.subTest(receipt=json.dumps(receipt)[:100]):
                with self.assertRaises(probes.ProbeError):
                    probes.receipts_to_packets(spec, [receipt])
        with self.assertRaises(probes.ProbeError):
            probes.receipts_to_packets(spec, [good, good])
        skipped = variant(lambda r: r["items"].__setitem__(0, {"item": r["items"][0]["item"], "attempted": False}))
        packets, _ = probes.receipts_to_packets(spec, [skipped])
        self.assertEqual(len(packets["calibration"]["observations"]), len(good["items"]) - 1)

    def test_convert_and_merge(self):
        probes_dir, spec_path, _ = self.frozen()
        spec = probes.load_spec(spec_path)
        receipt_path = Path(self.temp.name) / "r1.json"
        receipt_path.write_text(json.dumps(self.receipt(spec, "A", "2027-02-18T10:00:00-05:00")))
        out = Path(self.temp.name) / "evidence.json"
        probes.convert(spec_path, [receipt_path], out, echo=lambda *_: None)
        ladder = {"scaffolding"} if hasattr(pe, "PACKET_OPTIONS") else set()
        self.assertEqual(set(json.loads(out.read_text())), {"calibration", "transfer", "retrieval"} | ladder)
        with self.assertRaises(probes.ProbeError):
            probes.convert(spec_path, [receipt_path], out, echo=lambda *_: None)
        existing = Path(self.temp.name) / "existing.json"
        existing.write_text(json.dumps({"push-timeline": {"events": []}}))
        merged = probes.convert(spec_path, [receipt_path], Path(self.temp.name) / "merged.json", merge=existing,
                                echo=lambda *_: None)
        self.assertIn("push-timeline", merged)
        existing.write_text(json.dumps({"transfer": {"observations": []}}))
        with self.assertRaisesRegex(probes.ProbeError, "refusing"):
            probes.convert(spec_path, [receipt_path], Path(self.temp.name) / "m2.json", merge=existing,
                           echo=lambda *_: None)

    def test_evidence_feeds_the_audit(self):
        probes_dir, spec_path, _ = self.frozen()
        spec = probes.load_spec(spec_path)
        receipt = self.receipt(spec, "A", "2027-02-18T10:00:00-05:00")
        minute = 1
        for row in receipt["items"]:
            row.update(started_at=f"2027-02-18T10:{minute:02d}:00-05:00",
                       ended_at=f"2027-02-18T10:{minute + 2:02d}:00-05:00")
            minute += 3
        later = self.receipt(spec, "B", "2027-03-25T10:00:00-04:00")
        paths = []
        for n, value in enumerate((receipt, later)):
            paths.append(Path(self.temp.name) / f"r{n}.json")
            paths[-1].write_text(json.dumps(value))
        evidence = Path(self.temp.name) / "evidence.json"
        probes.convert(spec_path, paths, evidence, echo=lambda *_: None)
        audit_out = Path(self.temp.name) / "audit"
        with patch("sys.stdout"):
            self.assertEqual(frictional_cli.main(["audit", str(self.root), "--out", str(audit_out),
                                                  "--evidence", str(evidence)]), 0)
        report = json.loads((audit_out / "report.json").read_text())
        glp = {row["id"]: row["status"] for row in report["glp"]}
        for component in ("Y3", "Y4", "Y6"):
            self.assertEqual(glp[component], "evidence_recorded", component)
        supported = hasattr(pe, "PACKET_OPTIONS")
        self.assertEqual(glp["Y7"] == "evidence_recorded", supported)
        self.assertEqual(glp["Y1"] == "evidence_recorded", supported)
        self.assertNotIn("GLP score", json.dumps(report).replace("No GLP score", ""))

    def test_output_must_be_outside_project_and_starter(self):
        for out in (self.root / "probe-out", self.starter / "probe-out"):
            with self.assertRaisesRegex(probes.ProbeError, "outside"):
                probes.make(self.root, self.starter, out, run=False, echo=lambda *_: None)

    def test_cli(self):
        code = probes.main(["make", str(self.root), "--starter", str(self.starter), "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual(probes.main(["make", str(self.root), "--starter", str(self.starter),
                                      "--out", str(self.out), "--count", "9"]), 2)


@unittest.skipUnless(hasattr(pe, "PACKET_OPTIONS"), "process_evidence has no packet options yet")
class AdapterOptionTests(unittest.TestCase):
    LADDER = [{"level": "none", "successful": False}, {"level": "hint", "successful": True},
              {"level": "none", "successful": True}, {"level": "none", "successful": False},
              {"level": "hint", "successful": False}, {"level": "worked", "successful": True}]

    def test_ladder_reports_conditional_rates_and_no_ratio(self):
        text = pe.structured("scaffolding", {"observations": self.LADDER, "design": "ladder"})["facts"][0]
        self.assertIn("hint: 1/2 marked successful among those who reached it", text["observation"])
        self.assertNotIn("(hint - none)", text["observation"])
        self.assertTrue(any("conditional" in alt for alt in text["alternative_explanations"]))
        plain = pe.structured("scaffolding", {"observations": self.LADDER})["facts"][0]["observation"]
        self.assertIn("(hint - none) / (worked - none)", plain)
        for bad in ({"observations": self.LADDER, "design": "randomized"},
                    {"observations": self.LADDER, "design": "ladder", "extra": 1}):
            with self.assertRaises(ValueError):
                pe.structured("scaffolding", bad)

    def test_item_grain_wording(self):
        rows = [{"start": "2027-02-18T10:00:00-05:00", "end": "2027-02-18T10:02:00-05:00", "difficulty": "low"},
                {"start": "2027-02-18T10:03:00-05:00", "end": "2027-02-18T10:06:00-05:00", "difficulty": "medium"},
                {"start": "2027-02-18T10:07:00-05:00", "end": "2027-02-18T10:12:00-05:00", "difficulty": "high"}]
        item = pe.structured("temporal-engagement", {"milestones": rows, "grain": "item"})["facts"][0]["observation"]
        self.assertIn("timed item intervals", item)
        self.assertNotIn("coarser stand-in", item)
        milestone = pe.structured("temporal-engagement", {"milestones": rows})["facts"][0]["observation"]
        self.assertIn("coarser stand-in", milestone)
        with self.assertRaises(ValueError):
            pe.structured("temporal-engagement", {"milestones": rows, "grain": "week"})


@unittest.skipUnless(getattr(frictional_cli, "PROBE_COMMAND", False), "frictional_cli does not route probe yet")
class CliRouteTests(Base):
    def test_frictional_probe_routes(self):
        self.assertEqual(frictional_cli.main(["probe", "make", str(self.root), "--starter", str(self.starter),
                                              "--out", str(self.out)]), 0)
        self.assertTrue((self.out / "probes" / "probe-spec.json").is_file())


@unittest.skipUnless(RUNTIME, "no Docker or Podman daemon answered")
class ContainerProbeTests(unittest.TestCase):
    """Keys from a real container; nothing from the synthetic projects runs on the host."""

    def test_synthetic_project(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            root, starter, out = base / "project", base / "starter", base / "out"
            root.mkdir()
            write_project(root, starter)
            probes_dir = probes.make(root, starter, out, run=True, echo=lambda *_: None)
            spec = json.loads((probes_dir / "probe-spec.json").read_text())
            self.assertEqual(spec["status"], "draft", spec["notes"])
            self.assertEqual(sorted(f["name"] for f in spec["functions"]), ["band", "clamp", "tally"])
            self.assertEqual(probes.verify_keys(spec, probes_dir), [])
            receipt = json.loads((probes_dir / "run" / "receipt.json").read_text())
            self.assertEqual(receipt["network_check"], "verified-off")

    def test_word_counter_example(self):
        with TemporaryDirectory() as temp:
            probes_dir = probes.make(HERE / "examples" / "word-counter", HERE / "examples" / "word-counter-starter",
                                     Path(temp) / "out", run=True, echo=lambda *_: None)
            spec = json.loads((probes_dir / "probe-spec.json").read_text())
            self.assertEqual([f["name"] for f in spec["functions"]], ["most_common"], spec["notes"])
            self.assertTrue(any("Only 1 function" in n for n in spec["notes"]))


if __name__ == "__main__":
    unittest.main()
