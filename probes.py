"""Comprehension probes: an in-class probe built from a project's own code, and the
typed receipts that bring its results back into an audit. What the results can and cannot
say: MEASUREMENT.md, "Probe receipts".

Read-only toward the project. Source files are parsed with `ast` and `tokenize`,
never imported on the host. Every answer key comes from running probe_harness.py
inside Frictional's container (container_run.run_project) on a private temporary
copy of the project. Outputs go to OUT/probes/. Nothing here grades, scores or
judges authorship, and no GLP score is computed.

    python3 probes.py make TARGET --starter DIR --out OUT [--run [--run-spec FILE]]
                      [--count 3..5] [--seed N] [--deadline TS] [--only NAME]... [--inputs FILE]
    python3 probes.py references OUT --target TARGET [--run-spec FILE]
    python3 probes.py freeze OUT
    python3 probes.py receipts --spec OUT/probes/probe-spec.json RECEIPT... --out FILE [--merge FILE]

Inputs file (instructor-supplied extra inputs, Python literal source):
    {"counter.py:most_common": [{"args": "('a b b',)", "kwargs": "{}"}]}

Receipt (one per student per session; no names, answers, code or free text):
    {"frictional_probe_receipt": 1, "spec_sha256": HEX,
     "session": {"kind": "initial"|"reprobe", "form": "A"|"B", "started_at": ISO_TIME,
                 "conditions": {"ai": false, "notes": false, "proctored": BOOL}},
     "items": [{"item": ID, "attempted": BOOL, optional "confidence": 0|0.25|0.5|0.75|1,
                optional "started_at"/"ended_at": ISO_TIME,
                "correct": BOOL (predict, transfer) | "solved_at": "none"|"partial"|"full"|"unsolved" (bug)}]}
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import tempfile
import tokenize

import process_evidence as pe

SPEC_VERSION = 1
RECEIPT_VERSION = 1
PROBES = "probes"
SPEC_FILE = "probe-spec.json"
HARNESS = Path(__file__).resolve().parent / "probe_harness.py"
MARK = "FRICTIONAL-PROBE"
MIN_FUNCTIONS, MAX_FUNCTIONS, DEFAULT_FUNCTIONS = 3, 5, 4
MAX_CANDIDATES = 12
MAX_INPUTS = 8
MAX_INSTRUCTOR_INPUTS = 20
MAX_VARIANTS = 6
MAX_ARGS_REPR = 300
MAX_REPR = 200
MAX_JSON = 2 * 1024 * 1024
MAX_LOG_READ = 1024 * 1024
MIN_STATEMENTS = 3
BRANCHES = (2, 10)
TARGET_COMPLEXITY = 4
CONFIDENCE = (0, 0.25, 0.5, 0.75, 1)
DIFFICULTY = ("low", "medium", "high")
LADDER_MINUTES = (5, 10, 15)
REPROBE_WEEKS = (4, 6)
PREDICT_MINUTES = 3
TRANSFER_MINUTES = 10
BARNETT_CECI = ("knowledge-domain", "physical-context", "temporal-context", "functional-context",
                "social-context", "modality")
SOLVED_AT = ("none", "partial", "full", "unsolved")
HARNESS_REASONS = frozenset({"import", "import-timeout", "missing", "arguments", "timeout", "aborted",
                             "not-plain", "too-long", "skipped"})
TEXT_ORIGIN = re.compile(r"instructor|agent-draft:[A-Za-z0-9._-]{1,64}")
EXCEPTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
NONCE = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
CALL_ID = re.compile(r"c[0-9]{1,5}")
RESULT_LINE = re.compile(MARK + r" ([0-9a-f]{32}) (\{.*\})")
TEST_FILE = re.compile(r"(?:test_.*|.*_test|conftest)\.py")
README = re.compile(r"readme(?:\.(?:md|markdown|rst|txt))?", re.I)
FENCE = re.compile(r"^\s{0,3}(```+|~~~+)\s*([A-Za-z0-9_+-]*)")

DENY_NAMES = frozenset({
    "open", "print", "input", "exec", "eval", "compile", "__import__", "breakpoint", "exit", "quit",
    "globals", "locals", "vars", "setattr", "delattr", "help", "memoryview", "id", "hash",
    "os", "sys", "subprocess", "socket", "random", "time", "datetime", "uuid", "secrets", "threading",
    "multiprocessing", "asyncio", "requests", "urllib", "http", "shutil", "pathlib", "tempfile",
    "signal", "logging", "sqlite3", "io", "builtins", "importlib", "ctypes", "gc", "inspect",
})
DENY_MODULES = frozenset(n for n in DENY_NAMES if n.islower() and n not in {
    "open", "print", "input", "exec", "eval", "compile", "breakpoint", "exit", "quit", "globals",
    "locals", "vars", "setattr", "delattr", "help", "memoryview", "id", "hash"})

# Single-token mutation operators, in the order variants are preferred.
OPERATORS = (
    ("comparison-boundary", {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}),
    ("comparison-negation", {"==": "!=", "!=": "=="}),
    ("boolean-swap", {"and": "or", "or": "and"}),
    ("drop-not", {"not": ""}),
    ("constant-plus-one", {}),
    ("arithmetic-sign", {"+": "-", "-": "+"}),
)
HINTS = {
    "comparison-boundary": ("comparison", "the comparison operator"),
    "comparison-negation": ("comparison", "the comparison operator"),
    "boolean-swap": ("condition that joins two tests", "the word that joins the two tests"),
    "drop-not": ("condition", "the condition; something is missing from it"),
    "constant-plus-one": ("number", "the number on that line"),
    "arithmetic-sign": ("arithmetic step", "the arithmetic operator"),
}
LIMITS = [
    "Functions were chosen because they differ from the starter, not because the student wrote them; the tool cannot tell who wrote code.",
    "Every key is what the code returned inside Frictional's container under two hash seeds; nothing was imported or run on this computer.",
    "A probe result describes answers given under the recorded conditions. It is not a grade, a measure of learning, or evidence about authorship or AI use.",
    "Probe results are never the sole basis for a grade or an integrity action, and a probe is never given only to some students or triggered by an audit finding.",
    "Specs, sheets, keys, variants and receipts are education records. Keep them private and out of any repository.",
    "Nothing from a probe may be used for research before the IRB approves the study and the student consents.",
]


class ProbeError(ValueError):
    pass


# --------------------------------------------------------------------------
# Small helpers


def _order(seed, *parts) -> str:
    return hashlib.sha256("\x1f".join(map(str, (seed,) + parts)).encode("utf-8")).hexdigest()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def spec_digest(spec: dict) -> str:
    return _sha(canonical({k: v for k, v in spec.items() if k != "frozen"}))


def read_json(path: Path, cap: int = MAX_JSON):
    """Bounded JSON from a regular file (no symlink, no repeated keys, no NaN)."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with os.fdopen(os.open(Path(path).expanduser(), flags), "rb") as handle:
            meta = os.fstat(handle.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > cap:
                raise ProbeError(f"{Path(path).name}: expected a regular JSON file no larger than {cap} bytes.")
            payload = handle.read(cap + 1)
    except OSError as exc:
        raise ProbeError(f"Cannot read {Path(path).name} as a regular file (symlinks are refused).") from exc

    def reject(_value):
        raise ProbeError(f"{Path(path).name} contains a non-finite number.")

    def distinct(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ProbeError(f"{Path(path).name} repeats the key {key!r}.")
            out[key] = value
        return out

    try:
        return json.loads(payload.decode("utf-8"), parse_constant=reject, object_pairs_hook=distinct)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProbeError(f"{Path(path).name} is not valid UTF-8 JSON.") from exc


def _write_new(path: Path, text: str):
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(text)


def _replace(path: Path, text: str):
    """Rewrite one of Frictional's own output files atomically; never through a symlink."""
    if path.is_symlink():
        raise ProbeError(f"{path.name} is a symlink; refusing to write through it.")
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    _write_new(temp, text)
    os.replace(temp, path)


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _literal(text, kind):
    if not isinstance(text, str) or len(text) > MAX_ARGS_REPR:
        raise ProbeError("Arguments must be Python literal source of at most 300 characters.")
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
        raise ProbeError(f"Not a Python literal: {text[:60]!r}.") from exc
    if type(value) is not kind or (kind is dict and not all(isinstance(k, str) and k.isidentifier() for k in value)):
        raise ProbeError(f"Expected a literal {kind.__name__}: {text[:60]!r}.")
    return value


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


# --------------------------------------------------------------------------
# Static reading: which functions, which inputs


def is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(p in ("tests", "test") for p in parts[:-1]) or bool(TEST_FILE.fullmatch(parts[-1]))


def _parse(text: str, name: str):
    try:
        return ast.parse(text, filename=name)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _read_source(root: Path, rel: str):
    try:
        return pe.read_bounded(root, rel).decode("utf-8", "strict")
    except (OSError, ValueError, UnicodeError):
        return None


def _docstring_node(fn):
    first = fn.body[0] if fn.body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
            and isinstance(first.value.value, str):
        return first
    return None


def normalized(fn) -> str:
    clone = copy.deepcopy(fn)
    if _docstring_node(clone) is not None:
        clone.body = clone.body[1:] or [ast.Pass()]
    return ast.dump(clone, include_attributes=False)


def branch_points(fn) -> int:
    count = 0
    match_case = getattr(ast, "match_case", ())
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.IfExp, ast.ExceptHandler)):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            count += 1 + len(node.ifs)
        elif match_case and isinstance(node, match_case):
            count += 1
    return count


def statements(fn) -> int:
    total = sum(isinstance(node, ast.stmt) for node in ast.walk(fn)) - 1
    return total - (1 if _docstring_node(fn) is not None else 0)


def features(fn) -> list[str]:
    found = set()
    for loop in (n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.While))):
        for node in ast.walk(loop):
            if node is not loop and isinstance(node, (ast.For, ast.While)):
                found.add("nested-loop")
            if isinstance(node, ast.AugAssign) or (
                    isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("append", "add", "extend", "update")):
                found.add("loop-accumulator")
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            keys = {k.arg for k in node.keywords}
            if isinstance(f, ast.Name) and f.id == fn.name:
                found.add("recursion")
            if name in ("Counter", "defaultdict") or (name == "get" and len(node.args) == 2):
                found.add("counter")
            if name in ("sorted", "sort") and "key" in keys:
                found.add("sort-with-key")
            if name in ("min", "max") and "key" in keys:
                found.add("min-max-with-key")
            if name == "split":
                found.add("string-split")
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            if any(g.ifs for g in node.generators):
                found.add("comprehension-filter")
            if isinstance(node, ast.DictComp):
                found.add("dict-build")
        elif isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            if node.orelse[0].orelse:
                found.add("branch-chain")
    for stmt in fn.body[:-1]:
        if isinstance(stmt, ast.If) and any(isinstance(n, ast.Return) for n in ast.walk(stmt)):
            found.add("early-return")
    return sorted(found)


def _denied_locals(tree) -> set[str]:
    names = set(DENY_NAMES)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in DENY_MODULES:
                    names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in DENY_MODULES:
                names.update(alias.asname or alias.name for alias in node.names)
    return names


def _predictable(fn, denied) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return False
        if isinstance(node, ast.Name) and node.id in denied:
            return False
    return True


def signature_text(text: str, fn) -> str:
    lines = text.splitlines()
    first = fn.body[0]
    if first.lineno > fn.lineno:
        header = lines[fn.lineno - 1:first.lineno - 1]
    else:
        args = ast.get_source_segment(text, fn.args) or "..."
        header = [" " * fn.col_offset + f"def {fn.name}({args}):"]
    doc = _docstring_node(fn)
    body = lines[doc.lineno - 1:doc.end_lineno] if doc is not None and doc.lineno > fn.lineno else []
    indent = " " * (first.col_offset if first.lineno > fn.lineno else fn.col_offset + 4)
    return "\n".join(header + body + [indent + "..."])


def _calls(tree, name, stem):
    """Literal (args, kwargs, line) at call sites of `name` or `stem.name`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not ((isinstance(f, ast.Name) and f.id == name) or (
                isinstance(f, ast.Attribute) and f.attr == name and isinstance(f.value, ast.Name)
                and f.value.id == stem)):
            continue
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            continue
        try:
            args = tuple(ast.literal_eval(a) for a in node.args)
            kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
        yield args, kwargs, node.lineno


def _readme_blocks(text):
    """(first line number, code) for fenced Python-ish blocks; prompts stripped, output blanked."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m or m[2].lower() not in ("", "python", "py", "python3", "pycon", "pyrepl"):
            i += 1
            continue
        fence, start, body = m[1], i + 2, []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith(fence):
            body.append(lines[i])
            i += 1
        i += 1
        if any(l.lstrip().startswith(">>>") for l in body):
            body = [l.lstrip()[4:] if l.lstrip().startswith((">>> ", "... ")) else "" for l in body]
        yield start, "\n".join(body)


def _doctests(fn, path):
    doc = _docstring_node(fn)
    if doc is None:
        return
    for offset, line in enumerate(doc.value.value.splitlines()):
        stripped = line.strip()
        if stripped.startswith(">>> "):
            tree = _parse(stripped[4:], path)
            if tree is not None:
                for args, kwargs, _ in _calls(tree, fn.name, PurePosixPath(path).stem):
                    yield args, kwargs, f"{path}:{doc.lineno + offset} (docstring)"


def _perturbations(value):
    out = []
    kind = type(value)
    if kind is bool:
        out.append((not value, "flipped"))
    elif kind is int:
        out += [(0, "zero"), (-1, "minus one"), (value + 1, "plus one")]
    elif kind is float:
        out += [(0.0, "zero"), (value + 0.5, "plus one half")]
    elif kind is str:
        words = value.split()
        out.append((value.upper() if value != value.upper() else value.lower(), "case changed"))
        if len(words) >= 2:
            out.append((" ".join(words[:-1]), "last word dropped"))
            out.append((" ".join(reversed(words)), "word order reversed"))
        if words:
            out.append((words[0] + " " + value, "first word repeated"))
        out.append((value[:-1] if value.endswith("!") else value + "!", "punctuation changed"))
        out.append(("  " + value.replace(" ", "\t", 1) + "\n", "whitespace changed"))
        out.append(("", "empty"))
    elif kind in (list, tuple):
        out.append((kind(), "empty"))
        if value:
            out.append((kind([value[0]]), "one item"))
        if len(value) >= 2:
            out.append((kind(reversed(value)), "reversed"))
    elif kind is dict:
        out.append(({}, "empty"))
    return [(v, label) for v, label in out if not (type(v) is kind and v == value)]


def _round_trips(args, kwargs) -> bool:
    try:
        a, k = repr(args), repr(kwargs)
        return (len(a) <= MAX_ARGS_REPR and len(k) <= MAX_ARGS_REPR
                and ast.literal_eval(a) == args and ast.literal_eval(k) == kwargs)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False


def candidate_inputs(fn, path, seeds, extra, seed) -> list[dict]:
    """Instructor inputs first, then changed versions of the seed literals (never the seeds)."""
    seen = {(repr(a), repr(k)) for a, k, _ in seeds}
    chosen, keys = [], set()
    for item in extra:
        key = (item["args"], item["kwargs"])
        if key not in keys:
            keys.add(key)
            chosen.append(dict(item, origin="instructor"))
    changed = []
    for args, kwargs, origin in seeds:
        for i, value in enumerate(args):
            for new, label in _perturbations(value):
                changed.append((args[:i] + (new,) + args[i + 1:], kwargs, f"changed from {origin} ({label})"))
        for name, value in kwargs.items():
            for new, label in _perturbations(value):
                changed.append((args, dict(kwargs, **{name: new}), f"changed from {origin} ({label})"))
    ranked = []
    for args, kwargs, origin in changed:
        key = (repr(args), repr(kwargs))
        if key in seen or key in keys or not _round_trips(args, kwargs):
            continue
        keys.add(key)
        ranked.append((_order(seed, path, fn.name, *key), {"args": key[0], "kwargs": key[1], "origin": origin}))
    ranked.sort(key=lambda pair: pair[0])
    return (chosen + [item for _, item in ranked])[:max(MAX_INPUTS, len(chosen))]


def select_static(root: Path, starter: Path, files: dict, seed: int, nominated=None, extra_inputs=None):
    """Static candidates (ranked, at most MAX_CANDIDATES) and the exclusion counts."""
    excluded = {k: 0 for k in ("unchanged", "starter-unreadable", "decorated", "async-or-generator",
                               "dunder", "no-parameters", "trivial", "too-complex", "io-or-clock",
                               "no-inputs", "not-nominated", "over-candidate-cap")}
    stats = {"files": 0, "test-files": 0, "unparsable": 0, "functions": 0}
    sources, tests, readmes = [], [], []
    for rel in sorted(files):
        if not rel.endswith(".py"):
            if "/" not in rel and README.fullmatch(rel):
                readmes.append(rel)
            continue
        if is_test(rel):
            stats["test-files"] += 1
            tests.append(rel)
        else:
            stats["files"] += 1
            sources.append(rel)
    if not sources:
        raise ProbeError("No Python source files outside tests were found. Version 1 reads Python only.")
    seed_trees = []
    for rel in tests:
        text = _read_source(root, rel)
        tree = _parse(text, rel) if text is not None else None
        if tree is not None:
            seed_trees.append((rel, tree, 0))
    for rel in readmes:
        text = _read_source(root, rel)
        for start, code in _readme_blocks(text or ""):
            tree = _parse(code, rel)
            if tree is not None:
                seed_trees.append((rel, tree, start - 1))
    wanted = set(nominated or ())
    candidates = []
    for rel in sources:
        text = _read_source(root, rel)
        tree = _parse(text, rel) if text is not None else None
        if tree is None:
            stats["unparsable"] += 1
            continue
        old_text = None
        try:
            old_bytes = pe.read_bounded(starter, rel)
            old_text = old_bytes.decode("utf-8", "strict")
        except FileNotFoundError:
            old_tree = None
        except (OSError, ValueError, UnicodeError):
            old_tree = "unreadable"
        if old_text is not None:
            old_tree = _parse(old_text, rel) or "unreadable"
        denied = _denied_locals(tree)
        for fn in tree.body:
            if isinstance(fn, ast.AsyncFunctionDef):
                stats["functions"] += 1
                excluded["async-or-generator"] += 1
                continue
            if not isinstance(fn, ast.FunctionDef):
                continue
            stats["functions"] += 1
            if old_tree == "unreadable":
                excluded["starter-unreadable"] += 1
                continue
            if old_tree is not None:
                before = [n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == fn.name]
                if before and normalized(before[0]) == normalized(fn):
                    excluded["unchanged"] += 1
                    continue
            reason = None
            if fn.decorator_list:
                reason = "decorated"
            elif any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)) for n in ast.walk(fn)):
                reason = "async-or-generator"
            elif fn.name.startswith("__") and fn.name.endswith("__"):
                reason = "dunder"
            elif not (fn.args.posonlyargs or fn.args.args or fn.args.kwonlyargs):
                reason = "no-parameters"
            elif statements(fn) < MIN_STATEMENTS or branch_points(fn) < BRANCHES[0]:
                reason = "trivial"
            elif branch_points(fn) > BRANCHES[1]:
                reason = "too-complex"
            elif not _predictable(fn, denied):
                reason = "io-or-clock"
            elif wanted and fn.name not in wanted and f"{rel}:{fn.name}" not in wanted:
                reason = "not-nominated"
            if reason:
                excluded[reason] += 1
                continue
            stem = PurePosixPath(rel).stem
            seeds = []
            for where, seed_tree, offset in seed_trees:
                for args, kwargs, line in _calls(seed_tree, fn.name, stem):
                    seeds.append((args, kwargs, f"{where}:{line + offset}"))
            seeds += list(_doctests(fn, rel))
            extra = (extra_inputs or {}).get(f"{rel}:{fn.name}", [])
            inputs = candidate_inputs(fn, rel, seeds, extra, seed)
            if len(inputs) < 3:
                excluded["no-inputs"] += 1
                continue
            body = ast.get_source_segment(text, fn) or ""
            candidates.append({
                "path": rel, "name": fn.name, "lines": [fn.lineno, fn.end_lineno],
                "source_sha256": _sha(body), "features": features(fn), "complexity": branch_points(fn),
                "signature": signature_text(text, fn), "inputs": inputs,
                "_fn": fn, "_text": text,
            })
    candidates.sort(key=lambda c: (abs(c["complexity"] - TARGET_COMPLEXITY), -len(c["features"]),
                                   _order(seed, c["path"], c["name"])))
    excluded["over-candidate-cap"] = max(0, len(candidates) - MAX_CANDIDATES)
    stats.update(excluded=excluded, candidates=len(candidates))
    return candidates[:MAX_CANDIDATES], stats


def variants(candidate) -> list[dict]:
    """Single-token changes inside the function body that still parse, preferred order first."""
    fn, text = candidate["_fn"], candidate["_text"]
    doc = _docstring_node(fn)
    start = (doc.end_lineno + 1) if doc is not None else fn.body[0].lineno
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    rank = {name: i for i, (name, _) in enumerate(OPERATORS)}
    found = []
    for tok in tokens:
        (row, col), (erow, ecol) = tok.start, tok.end
        if row != erow or not start <= row <= fn.end_lineno:
            continue
        for name, table in OPERATORS:
            if name == "constant-plus-one":
                if tok.type == tokenize.NUMBER and re.fullmatch(r"[0-9]+", tok.string) \
                        and (tok.string == "0" or not tok.string.startswith("0")):
                    found.append((name, row, col, ecol, tok.string, str(int(tok.string) + 1)))
            elif tok.string in table and tok.type in (tokenize.OP, tokenize.NAME):
                found.append((name, row, col, ecol, tok.string, table[tok.string]))
    lines = text.splitlines(keepends=True)
    out = []
    for name, row, col, ecol, old, new in sorted(found, key=lambda f: (rank[f[0]], f[1], f[2])):
        line = lines[row - 1]
        if name == "drop-not":
            ecol += len(line[ecol:]) - len(line[ecol:].lstrip(" "))
        changed = "".join(lines[:row - 1]) + line[:col] + new + line[ecol:] + "".join(lines[row:])
        tree = _parse(changed, candidate["path"])
        if tree is None:
            continue
        same = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn.name]
        if not same or normalized(same[0]) == normalized(fn):
            continue
        out.append({"operator": name, "line": row, "from": old, "to": new or "(removed)", "text": changed,
                    "region": _region(fn, row)})
        if len(out) >= MAX_VARIANTS:
            break
    return out


def _region(fn, row):
    best = None
    for node in ast.walk(fn):
        if isinstance(node, ast.stmt) and node is not fn and node.lineno <= row <= node.end_lineno:
            if best is None or (node.end_lineno - node.lineno) <= (best.end_lineno - best.lineno):
                best = node
    if best is None:
        return [row, row]
    return [best.lineno, best.end_lineno]


# --------------------------------------------------------------------------
# Keys: one container run of probe_harness.py on a private copy


def resolve_run_spec(root: Path, files: dict, run_spec: Path | None):
    import container_run as cr
    if run_spec is not None:
        return cr.load_spec(run_spec), "supplied run spec"
    if "frictional-run.json" in files:
        return cr.load_spec(root / "frictional-run.json"), "the project's own frictional-run.json"
    spec = cr.default_spec(root, files)
    return spec, "default from project manifests" if spec else None


def harness_spec(spec: dict) -> dict:
    run = [{"cmd": f"PYTHONHASHSEED={seed} python .frictional-probe/probe_harness.py "
                   f".frictional-probe/calls-{seed}.json",
            "source": f"frictional: probe harness, hash seed {seed}"} for seed in (0, 1)]
    return {"image": spec["image"], "setup": spec["setup"], "run": run,
            "timeout_seconds": spec["timeout_seconds"],
            "network": "none" if spec["network"] == "none" else "setup"}


def key_run(root: Path, spec: dict, calls: list[dict], files_to_add: dict[str, str], out_dir: Path,
            revision: str | None, runtime: dict) -> dict:
    """Run the harness twice in the container; return {"nonces", "logs", "receipt", "error"}."""
    import container_run as cr
    temp = Path(tempfile.mkdtemp(prefix="frictional-probe-"))
    try:
        work = temp / "work"
        work.mkdir(mode=0o700)
        cr._copy_tree(root, work)
        probe = work / ".frictional-probe"
        if probe.is_symlink() or probe.is_file():
            probe.unlink()
        elif probe.exists():
            shutil.rmtree(probe)
        probe.mkdir()
        shutil.copyfile(HARNESS, probe / "probe_harness.py")
        for rel, text in files_to_add.items():
            target = probe / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_new(target, text)
        nonces = []
        for seed in (0, 1):
            nonce = secrets.token_hex(16)
            nonces.append(nonce)
            _write_new(probe / f"calls-{seed}.json", json.dumps({"nonce": nonce, "calls": calls}))
        receipt = cr.run_project(work, harness_spec(spec), out_dir, runtime=runtime, revision=revision)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    logs = [c["log"] for c in receipt.get("commands", []) if c.get("phase") == "run"]
    error = None
    if receipt.get("error"):
        error = f"the container run did not start ({receipt['error']})"
    elif receipt.get("stopped"):
        error = f"the container run stopped ({receipt['stopped']})"
    elif len(logs) != 2:
        error = "the harness did not run twice"
    return {"dir": "", "nonces": nonces, "logs": logs, "calls": len(calls), "receipt": "run/receipt.json",
            "error": error}


def parse_log(text: str, nonce: str, expected: set[str]) -> tuple[dict, bool, list[str]]:
    """Harness result lines for one run: (results by call id, finished, problems)."""
    results, done, problems = {}, False, []
    for line in text.splitlines():
        if MARK not in line:
            continue
        m = RESULT_LINE.fullmatch(line)
        if not m or m[1] != nonce:
            problems.append("a result line had the wrong form or nonce")
            continue
        try:
            obj = json.loads(m[2])
        except ValueError:
            problems.append("masked" if "[masked]" in line else "a result line was not JSON")
            continue
        if isinstance(obj, dict) and set(obj) == {"done"}:
            done = True
            continue
        cid = obj.get("id") if isinstance(obj, dict) else None
        if cid not in expected or cid in results:
            problems.append("a result line named an unknown or repeated call")
            continue
        status = obj.get("status")
        if status == "value" and set(obj) == {"id", "status", "repr", "sha256"} \
                and isinstance(obj["repr"], str) and len(obj["repr"]) <= MAX_REPR \
                and isinstance(obj["sha256"], str) and HEX64.fullmatch(obj["sha256"]):
            if _sha(obj["repr"]) != obj["sha256"]:
                problems.append("masked")
            else:
                results[cid] = {"status": "value", "repr": obj["repr"]}
        elif status == "raises" and set(obj) == {"id", "status", "type"} \
                and isinstance(obj["type"], str) and EXCEPTION.fullmatch(obj["type"]):
            results[cid] = {"status": "raises", "type": obj["type"]}
        elif status == "error" and set(obj) == {"id", "status", "reason"} and obj["reason"] in HARNESS_REASONS:
            results[cid] = {"status": "error", "reason": obj["reason"]}
        else:
            problems.append("a result line had unexpected fields")
    return results, done, problems


def observed(probes_dir: Path, run: dict, expected: set[str]) -> tuple[dict, list[str]]:
    """Stable results: both hash-seed runs agree. Returns (results, notes).

    `run` is a key-run record: {"dir": "" or "references-run-N", "nonces", "logs"}; the
    logs are paths relative to that folder, checked against its run/receipt.json.
    """
    if not isinstance(run, dict) or run.get("error"):
        return {}, [run.get("error") if isinstance(run, dict) else "there is no key run"]
    total = run.get("calls")
    if type(total) is not int or not 0 < total <= 100000:
        raise ProbeError("The key run record in the spec is malformed.")
    wanted, expected = set(expected), {f"c{n}" for n in range(1, total + 1)}
    if not wanted <= expected:
        raise ProbeError("A key refers to a call that is not in its key run.")
    folder = run.get("dir", "")
    if not isinstance(folder, str) or not re.fullmatch(r"(?:references-run-[0-9]{1,3})?", folder):
        raise ProbeError("The key run record in the spec is malformed.")
    base = probes_dir / folder if folder else probes_dir
    receipt = read_json(base / "run" / "receipt.json")
    shas = {c["log"]: c.get("log_sha256") for c in receipt.get("commands", []) if c.get("phase") == "run"}
    per_run, notes = [], []
    logs, nonces = run.get("logs"), run.get("nonces")
    if not (isinstance(logs, list) and isinstance(nonces, list) and len(logs) == len(nonces) == 2):
        raise ProbeError("The key run record in the spec is malformed.")
    for rel, nonce in zip(logs, nonces):
        if not (isinstance(nonce, str) and NONCE.fullmatch(nonce) and isinstance(rel, str)
                and re.fullmatch(r"run/logs/run-[12]\.log", rel)):
            raise ProbeError("The key run record in the spec is malformed.")
        data = pe.read_bounded(base, rel, MAX_LOG_READ)
        if hashlib.sha256(data).hexdigest() != shas.get(rel):
            raise ProbeError(f"{folder + '/' if folder else ''}{rel} does not match its checksum in run/receipt.json.")
        results, done, problems = parse_log(data.decode("utf-8", "replace"), nonce, expected)
        masked = [p for p in problems if p == "masked"]
        problems = [p for p in problems if p != "masked"]
        if problems:
            notes.append(f"{rel}: {problems[0]}; no keys were taken from that run")
            results = {}
        elif not done:
            notes.append(f"{rel}: the harness did not finish; calls without a line have no key")
        if masked:
            notes.append(f"{rel}: {len(masked)} result line(s) were changed by secret masking; those calls have no key")
        per_run.append(results)
    stable = {}
    if len(per_run) == 2:
        first, second = per_run
        for cid in wanted:
            a, b = first.get(cid), second.get(cid)
            if a is not None and a == b and a["status"] in ("value", "raises"):
                stable[cid] = a
    return stable, notes


def _output(result) -> tuple:
    return (result["status"], result.get("repr", result.get("type")))


def show(result) -> str:
    return result["repr"] if result["status"] == "value" else f"raises {result['type']}"


# --------------------------------------------------------------------------
# make


def make(target: Path, starter: Path, out: Path, *, run: bool, run_spec: Path | None = None,
         count: int = DEFAULT_FUNCTIONS, seed: int = 1, deadline: str | None = None,
         nominated=None, inputs_file: Path | None = None, runtime=None, echo=print) -> Path:
    import container_run as cr
    if not MIN_FUNCTIONS <= count <= MAX_FUNCTIONS:
        raise ProbeError("--count must be 3, 4 or 5.")
    root = target.expanduser().resolve(strict=True)
    if not root.is_dir() or target.is_symlink():
        raise ProbeError("TARGET must be a local directory.")
    if starter.is_symlink() or not starter.is_dir():
        raise ProbeError("--starter must point to a normal local directory.")
    starter = starter.resolve(strict=True)
    if deadline is not None:
        try:
            pe.timestamp(deadline)
        except ValueError:
            raise ProbeError("--deadline must look like 2027-02-14T23:59:00-05:00 (with a timezone).") from None
    out = out.expanduser()
    if out.is_symlink():
        raise ProbeError("--out must not be a symlink.")
    if out.exists():
        if not out.is_dir():
            raise ProbeError("--out exists and is not a directory.")
    elif not out.parent.is_dir():
        raise ProbeError("The parent of --out must exist.")
    resolved = out.resolve()
    if _within(resolved, root) or _within(resolved, starter):
        raise ProbeError("Choose an output directory outside the project and the starter.")
    probes_dir = resolved / PROBES
    if os.path.lexists(probes_dir):
        raise ProbeError(f"{probes_dir} already exists; Frictional will not overwrite it.")
    extra = _load_inputs(inputs_file) if inputs_file else {}
    files, summary = pe.inventory(root)
    candidates, stats = select_static(root, starter, files, seed, nominated, extra)
    try:
        revision = pe.git_head(root).decode("ascii")
    except (OSError, ValueError):
        revision = None

    spec = {
        "frictional_probe_spec": SPEC_VERSION,
        "status": "draft-unkeyed",
        "tool_version": "probes 1",
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "target": {"revision": revision, "starter": "provided", "deadline": deadline},
        "selection": {"seed": seed, "language": "python", "count_requested": count,
                      "source_files": stats["files"], "test_files": stats["test-files"],
                      "unparsable_files": stats["unparsable"], "functions_examined": stats["functions"],
                      "candidates": stats["candidates"], "excluded": stats["excluded"],
                      "nominated": sorted(nominated) if nominated else None,
                      "nomination": "self-report by the student" if nominated else None,
                      "inventory_complete": bool(summary[-1] and not summary[2])},
        "settings": {"predict_view": "signature", "confidence_scale": list(CONFIDENCE),
                     "ladder_minutes": list(LADDER_MINUTES), "reprobe_weeks": list(REPROBE_WEEKS)},
        "key_run": None,
        "functions": [],
        "items": [],
        "notes": [],
        "limits": LIMITS,
    }
    out.mkdir(mode=0o700, exist_ok=True)
    probes_dir.mkdir(mode=0o700)

    spec["candidates_preview"] = [{"path": c["path"], "name": c["name"], "features": c["features"],
                                   "inputs": len(c["inputs"])} for c in candidates]
    if not run:
        spec["notes"].append("No container run was requested, so there are no keys and no items yet. "
                             "Run again with --run to compute keys and choose items.")
        return _finish(probes_dir, spec, echo)
    runtime = runtime if runtime is not None else cr.find_runtime()
    run_spec_value, spec_source = resolve_run_spec(root, files, run_spec)
    if runtime is None or run_spec_value is None:
        spec["notes"].append("No working Docker or Podman was found, so nothing was run and there are no keys."
                             if runtime is None else
                             "No run spec, frictional-run.json or Python manifest was found, so nothing was run.")
        return _finish(probes_dir, spec, echo)
    if not candidates:
        spec["notes"].append("No function passed the selection rules; see selection.excluded.")
        return _finish(probes_dir, spec, echo)

    calls, index, staged = [], {}, {}
    for f_i, cand in enumerate(candidates):
        cand["variants"] = variants(cand)
        for i_i, item in enumerate(cand["inputs"]):
            cid = f"c{len(calls) + 1}"
            calls.append({"id": cid, "module": cand["path"], "function": cand["name"],
                          "args": item["args"], "kwargs": item["kwargs"]})
            index[(f_i, None, i_i)] = cid
        for v_i, var in enumerate(cand["variants"]):
            rel = f"variants/v{f_i}-{v_i}/{cand['path']}"
            staged[rel] = var["text"]
            for i_i, item in enumerate(cand["inputs"]):
                cid = f"c{len(calls) + 1}"
                calls.append({"id": cid, "module": cand["path"], "function": cand["name"],
                              "args": item["args"], "kwargs": item["kwargs"],
                              "file": f".frictional-probe/{rel}"})
                index[(f_i, v_i, i_i)] = cid
    echo(f"frictional: computing keys for {len(calls)} calls in a container (twice, two hash seeds)...")
    record = key_run(root, run_spec_value, calls, staged, probes_dir, revision, runtime)
    record["spec_source"] = spec_source
    record["image"] = run_spec_value["image"]
    spec["key_run"] = record
    results, notes = observed(probes_dir, record, {c["id"] for c in calls})
    spec["notes"] += notes
    if not results:
        spec["notes"].append("No stable keys were observed, so no items were written.")
        return _finish(probes_dir, spec, echo)
    _choose(spec, candidates, index, results, seed, count, probes_dir)
    spec["status"] = "draft" if spec["items"] else "draft-unkeyed"
    return _finish(probes_dir, spec, echo)


def _load_inputs(path: Path) -> dict:
    data = read_json(path)
    if not isinstance(data, dict):
        raise ProbeError("The inputs file must be a JSON object.")
    out = {}
    for key, rows in data.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*", key):
            raise ProbeError("Inputs keys look like path/to/module.py:function_name.")
        if not isinstance(rows, list) or len(rows) > MAX_INSTRUCTOR_INPUTS:
            raise ProbeError(f"Inputs for {key} must be a list of at most {MAX_INSTRUCTOR_INPUTS}.")
        clean = []
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"args", "kwargs"} or "args" not in row:
                raise ProbeError(f"Each input for {key} is {{\"args\": \"(...)\", \"kwargs\": \"{{...}}\"}}.")
            args = _literal(row["args"], tuple)
            kwargs = _literal(row.get("kwargs", "{}"), dict)
            clean.append({"args": repr(args), "kwargs": repr(kwargs)})
        out[key] = clean
    return out


def _choose(spec, candidates, index, results, seed, count, probes_dir: Path):
    """Pick functions, forms A and B, the bug item and the transfer frames from stable keys."""
    eligible = []
    for f_i, cand in enumerate(candidates):
        keyed = [(i_i, results[index[(f_i, None, i_i)]]) for i_i in range(len(cand["inputs"]))
                 if index[(f_i, None, i_i)] in results]
        if len(keyed) >= 3 and len({_output(r) for _, r in keyed}) >= 2:
            eligible.append((f_i, cand, keyed))
        else:
            spec["selection"]["excluded"].setdefault("unkeyed", 0)
            spec["selection"]["excluded"]["unkeyed"] += 1
    ranked = sorted(eligible, key=lambda e: (abs(e[1]["complexity"] - TARGET_COMPLEXITY),
                                            -len(e[1]["features"]), _order(seed, e[1]["path"], e[1]["name"])))
    picked, used_files = [], set()
    for entry in ranked:
        if len(picked) < count and entry[1]["path"] not in used_files:
            picked.append(entry)
            used_files.add(entry[1]["path"])
    for entry in ranked:
        if len(picked) < count and entry not in picked:
            picked.append(entry)
    if 0 < len(picked) < MIN_FUNCTIONS:
        spec["notes"].append(f"Only {len(picked)} function(s) qualified; the design asks for 3 to 5.")
    forms = {}
    for n, (f_i, cand, keyed) in enumerate(picked, 1):
        fid = f"f{n}"
        spec["functions"].append({"id": fid, "path": cand["path"], "name": cand["name"], "lines": cand["lines"],
                                  "source_sha256": cand["source_sha256"], "features": cand["features"],
                                  "signature": cand["signature"]})
        order = sorted(keyed, key=lambda k: (k[1]["status"] != "value", _order(seed, fid, k[0])))
        a = order[0]
        b = next((k for k in order[1:] if _output(k[1]) != _output(a[1])), order[1])
        forms[fid] = (f_i, cand, keyed, a, b)
        for form, (i_i, result) in (("A", a), ("B", b)):
            spec["items"].append({
                "id": f"{fid}-{form}", "kind": "predict", "form": form, "function": fid,
                "call": {"args": cand["inputs"][i_i]["args"], "kwargs": cand["inputs"][i_i]["kwargs"]},
                "input_origin": cand["inputs"][i_i]["origin"],
                "key": result, "key_call": index[(f_i, None, i_i)],
                "difficulty": None, "timebox_minutes": PREDICT_MINUTES})
    bug = None
    for fid in sorted(forms, key=lambda f: _order(seed, "bug", f)):
        f_i, cand, keyed, a, b = forms[fid]
        options = []
        for v_i, var in enumerate(cand["variants"]):
            for i_i, original in keyed:
                if i_i in (a[0], b[0]):
                    continue
                cid = index[(f_i, v_i, i_i)]
                changed = results.get(cid)
                if changed is None or _output(changed) == _output(original):
                    continue
                both_values = original["status"] == "value" and changed["status"] == "value"
                options.append((not both_values, v_i, _order(seed, fid, v_i, i_i), i_i, cid))
        if options:
            options.sort()
            best = options[0]
            if bug is None or (bug[0][0] and not best[0]):
                bug = (best, fid)
            if not best[0]:
                break
    if bug is not None:
        (only_raises, v_i, _, i_i, cid), fid = bug
        f_i, cand, keyed, a, b = forms[fid]
        var = cand["variants"][v_i]
        rel = f"variants/bug-1/{cand['path']}"
        target = probes_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_new(target, var["text"])
        kind, detail = HINTS[var["operator"]]
        lo, hi = var["region"]
        where = f"line {lo}" if lo == hi else f"lines {lo} to {hi}"
        call = _call_text(cand["name"], cand["inputs"][i_i])
        original = dict(keyed)[i_i]
        spec["items"].append({
            "id": "bug-1", "kind": "bug", "form": "A", "function": fid, "variant": rel,
            "variant_sha256": _sha(var["text"]),
            "mutation": {"operator": var["operator"], "line": var["line"], "from": var["from"], "to": var["to"]},
            "witness": {"call": {"args": cand["inputs"][i_i]["args"], "kwargs": cand["inputs"][i_i]["kwargs"]},
                        "original": original, "variant": results[cid]},
            "key_calls": {"original": index[(f_i, None, i_i)], "variant": cid},
            "hints": {"partial": f"One {kind} in {where} is wrong.",
                      "full": f"Line {var['line']}: {detail}. Trace {call} through that line."},
            "ladder_minutes": list(LADDER_MINUTES), "difficulty": None})
        if only_raises:
            spec["notes"].append("The bug variant only changes the output by raising an error; no variant "
                                 "changed one value into another.")
    else:
        spec["notes"].append("No bug variant changed an output on an input outside forms A and B, "
                             "so there is no bug item.")
    if forms:
        bug_fid = bug[1] if bug else None
        frame_fid = sorted(forms, key=lambda f: (-len(forms[f][1]["features"]), f == bug_fid,
                                                 _order(seed, "transfer", f)))[0]
        frame = {"features": forms[frame_fid][1]["features"]}
        for kind, iid in (("transfer-near", "near-1"), ("transfer-far", "far-1")):
            item = {"id": iid, "kind": kind, "form": "A", "function": frame_fid, "frame": frame,
                    "text": None, "text_origin": None, "approved_by_instructor": False,
                    "reference": {"path": None, "function": None, "calls": [], "keys": [], "validated": False},
                    "difficulty": None, "timebox_minutes": TRANSFER_MINUTES}
            if kind == "transfer-far":
                item["barnett_ceci"] = None
            spec["items"].append(item)


def _call_text(name, item):
    args = ast.literal_eval(item["args"])
    kwargs = ast.literal_eval(item["kwargs"])
    parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
    return f"{name}({', '.join(parts)})"


def _finish(probes_dir: Path, spec: dict, echo) -> Path:
    _write_new(probes_dir / SPEC_FILE, json.dumps(spec, indent=2) + "\n")
    render(probes_dir, spec, first=True)
    keyed = sum(1 for i in spec["items"] if i["kind"] in ("predict", "bug"))
    echo(f"frictional: wrote {probes_dir} (status {spec['status']}, {len(spec['functions'])} functions, "
         f"{keyed} keyed items).")
    for note in spec["notes"]:
        echo(f"  note: {note}")
    return probes_dir


# --------------------------------------------------------------------------
# Sheets


def _function(spec, fid):
    return next(f for f in spec["functions"] if f["id"] == fid)


def _numbered(text: str, first: int, last: int) -> str:
    lines = text.splitlines()[first - 1:last]
    width = len(str(last))
    return "\n".join(f"{n:>{width}}  {line}" for n, line in enumerate(lines, first))


def _code(text, lang="python"):
    fence = _fence(text)
    return f"{fence}{lang}\n{text}\n{fence}"


CONFIDENCE_LINE = "Confidence before answering: [ ] 0%  [ ] 25%  [ ] 50%  [ ] 75%  [ ] 100%"


SHEETS = (("A", 1, "student-sheet-A-part1.md"), ("A", 2, "student-sheet-A-part2.md"), ("B", 1, "student-sheet-B.md"))


def student_sheet(probes_dir: Path, spec: dict, form: str, part: int = 1) -> str:
    """Part 1 holds the predict items; part 2 (form A only) the bug and transfer items, which show
    function bodies and so are handed out only after part 1 is collected."""
    title = f"# Probe, form {form}" + (f", part {part}" if form == "A" else "")
    out = [title, "",
           "Work on your own: no AI tools, no notes, no internet. The functions below come from your "
           "project and are not in the course starter. This probe counts for completion only.", "",
           "Before each item, tick how sure you are that you will get it right. Then answer.", ""]
    if form == "A" and part == 1:
        out += ["Hand this part in before you receive part 2.", ""]
    items = [i for i in spec["items"] if i["form"] == form and (i["kind"] == "predict") == (part == 1)]
    if not items:
        out.append("_No items yet: the probe has not been keyed._")
    for item in items:
        fn = _function(spec, item["function"])
        out += ["---", ""]
        if item["kind"] == "predict":
            out += [f"## Item {item['id']} (about {item['timebox_minutes']} minutes)", "",
                    f"From `{fn['path']}`:", "", _code(fn["signature"]), "",
                    "What does this call return? If it raises an error, name the error.", "",
                    _code(_call_text(fn["name"], item["call"])), "", CONFIDENCE_LINE, "", "Answer:", ""]
        elif item["kind"] == "bug":
            text = pe.read_bounded(probes_dir, item["variant"]).decode("utf-8")
            lo, hi = fn["lines"]
            out += [f"## Item {item['id']} (up to {item['ladder_minutes'][-1]} minutes)", "",
                    f"This copy of `{fn['name']}` from `{fn['path']}` has one bug. Line numbers are on the left.", "",
                    _code(_numbered(text, lo, hi), ""), "",
                    f"`{_call_text(fn['name'], item['witness']['call'])}` returns "
                    f"`{show(item['witness']['variant'])}`. It should return `{show(item['witness']['original'])}`.", "",
                    "Which line is wrong, and what should it say?", "", CONFIDENCE_LINE, "", "Answer:", ""]
        else:
            label = "Near transfer" if item["kind"] == "transfer-near" else "Far transfer"
            out += [f"## Item {item['id']}: {label} (about {item['timebox_minutes']} minutes)", "",
                    item["text"] or "_The instructor writes this task before freezing the probe._", "",
                    CONFIDENCE_LINE, "", "Answer:", ""]
    return "\n".join(out) + "\n"


def instructor_key(spec: dict) -> str:
    sel = spec["selection"]
    excluded = ", ".join(f"{k} {v}" for k, v in sel["excluded"].items() if v) or "none"
    out = ["# Probe: instructor key", "",
           "**Keep this file private.** It holds keys and code from the student's project. "
           "It is an education record; never commit it to a repository.", "",
           f"Status: **{spec['status']}**." + (f" Spec SHA-256: `{spec['frozen']['sha256']}`." if spec.get("frozen") else ""), "",
           "## Rules for using this probe", "",
           "- Give it to every student in the section, on a date announced in advance; never only to some "
           "students, and never because of an audit finding.",
           "- No AI tools, no notes. Students tick a confidence before each item.",
           "- Form A has two parts. Collect part 1 (predict items, signatures only) before handing out "
           "part 2: the bug item shows a function's body. Form B is the re-probe, 4 to 6 weeks later.",
           "- Completion credit only. A result is never the sole basis for a grade or an integrity action. "
           "A student may miss their own code's output because of nerves, time since writing it, permitted "
           "AI help, or a teammate's work.",
           "- Nothing from this probe may be used for research before IRB approval and the student's consent.", "",
           "## How the functions were chosen", "",
           f"Python functions that differ from the starter: {sel['candidates']} candidate(s) of "
           f"{sel['functions_examined']} examined. Excluded: {excluded}. Seed {sel['seed']}."
           + (f" The student nominated: {', '.join(sel['nominated'])} (self-report)." if sel.get("nominated") else ""), ""]
    if spec.get("key_run"):
        kr = spec["key_run"]
        out += [f"Keys come from one container run ({kr.get('image')}, setup from {kr.get('spec_source')}), "
                "two hash seeds; a key is kept only when both runs agree. See `run/receipt.json`.", ""]
    for note in spec["notes"]:
        out.append(f"- Note: {note}")
    if spec["notes"]:
        out.append("")
    out += ["## Before you freeze", "",
            "Edit `probe-spec.json`: set every item's `difficulty` (low, medium or high); write both "
            "transfer tasks (`text`, `text_origin` \"instructor\" or \"agent-draft:MODEL\" with "
            "`approved_by_instructor` true), their reference solutions under `references/` with `reference.path`, "
            "`reference.function` and `reference.calls`, and the far task's `barnett_ceci` dimension. "
            "Delete any item you do not want. Then run `probe references` and `probe freeze`. "
            "Receipt templates are written when you freeze.", "",
            "## Items", ""]
    for item in spec["items"]:
        fn = _function(spec, item["function"])
        out.append(f"### {item['id']} ({item['kind']}, form {item['form']}, difficulty: {item['difficulty'] or 'not set'})")
        out.append("")
        if item["kind"] == "predict":
            out += [f"- Call: `{_call_text(fn['name'], item['call'])}` (input {item['input_origin']})",
                    f"- Key: `{show(item['key'])}`",
                    "- Mark correct when the value matches the key. Whether quotes or spacing matter is your "
                    "call; use the same rule for everyone.", ""]
        elif item["kind"] == "bug":
            m = item["mutation"]
            change = (f"`{m['from']}` was removed" if m["to"] == "(removed)" else f"`{m['from']}` became `{m['to']}`")
            out += [f"- Variant: `{item['variant']}`; {m['operator']} at line {m['line']}: {change}",
                    f"- Witness: `{_call_text(fn['name'], item['witness']['call'])}` returns `{show(item['witness']['variant'])}`; "
                    f"the original returns `{show(item['witness']['original'])}`",
                    f"- No hint for {item['ladder_minutes'][0]} minutes. Partial hint at {item['ladder_minutes'][0]}: "
                    f"\"{item['hints']['partial']}\" Full hint at {item['ladder_minutes'][1]}: \"{item['hints']['full']}\" "
                    f"Stop at {item['ladder_minutes'][2]}.",
                    "- Record `solved_at`: none, partial, full or unsolved. Fixed means the right line and a change "
                    "that restores the original output for the witness call.", ""]
        else:
            ref = item["reference"]
            out += [f"- Frame: function `{fn['name']}` in `{fn['path']}`; structures found: "
                    f"{', '.join(item['frame']['features']) or 'none detected'}",
                    "- Near: a changed requirement for the same function." if item["kind"] == "transfer-near" else
                    "- Far: the same structure in a different setting; name the Barnett and Ceci dimension "
                    f"({', '.join(BARNETT_CECI)}). Set: {item.get('barnett_ceci') or 'not yet'}",
                    f"- Task text: {'written (' + str(item['text_origin']) + ')' if item['text'] else 'not yet written'}",
                    f"- Reference solution: {ref['path'] or 'not yet'}; checked in the container: "
                    f"{'yes' if ref['validated'] else 'no'}"]
            for n, key in enumerate(ref.get("keys", []), 1):
                out.append(f"  - check {n}: `{key['call']}` returns `{show(key['result'])}`")
            out.append("")
    out += ["## Limits", ""] + [f"- {line}" for line in spec["limits"]]
    return "\n".join(out) + "\n"


RUBRIC = """# Oral walkthrough rubric (teaching aid)

Ask the student to walk you through one function from the probe: what it is for, how it works, what
was hard to get right, and what they would change. Score each marker 0, 1 or 2. This is a teaching
aid: scores are not a packet in the audit, and for research they need two raters who do not grade
the course.

| Marker | 0 absent | 1 present but generic | 2 specific and anchored |
|---|---|---|---|
| Personal encounter | No confusion, surprise or decision of their own | A general remark ("it was tricky") | A particular confusion, surprise or decision from their attempt |
| Below-surface engagement | Describes only what the result is | Names a step without saying why | Names a mechanism or reason |
| Productive uncertainty | Nothing left unclear, or vague doubt | Says something is unclear, without saying what | States specifically what they still do not understand |
| Change over time | No change of position or plan | Says something changed, without what or why | Says what changed in their approach and why |

A fluent walkthrough can be rehearsed, and a halting one can reflect real work. Never use these
scores to infer authorship, AI use or misconduct.
"""


def receipt_template(spec: dict, form: str) -> str:
    items = []
    for item in spec["items"]:
        if item["form"] != form:
            continue
        row = {"item": item["id"], "attempted": True, "confidence": None,
               "started_at": None, "ended_at": None}
        row["solved_at" if item["kind"] == "bug" else "correct"] = None
        items.append(row)
    template = {"frictional_probe_receipt": RECEIPT_VERSION, "spec_sha256": spec["frozen"]["sha256"],
                "session": {"kind": "initial" if form == "A" else "reprobe", "form": form,
                            "started_at": None, "conditions": {"ai": False, "notes": False, "proctored": True}},
                "items": items}
    return json.dumps(template, indent=2) + "\n"


def render(probes_dir: Path, spec: dict, first: bool = False):
    write = (lambda p, t: _write_new(p, t)) if first else _replace
    write(probes_dir / "instructor-key.md", instructor_key(spec))
    for form, part, name in SHEETS:
        write(probes_dir / name, student_sheet(probes_dir, spec, form, part))
    if first:
        _write_new(probes_dir / "walkthrough-rubric.md", RUBRIC)
    if spec.get("frozen"):
        for form in ("A", "B"):
            if any(i["form"] == form for i in spec["items"]):
                _write_new(probes_dir / f"receipt-template-{form}.json", receipt_template(spec, form))


# --------------------------------------------------------------------------
# Loading and checking a spec


def _probes_dir(out: Path) -> Path:
    out = out.expanduser()
    probes_dir = out if out.name == PROBES else out / PROBES
    if probes_dir.is_symlink() or not probes_dir.is_dir():
        raise ProbeError(f"No probes folder at {probes_dir}.")
    return probes_dir.resolve()


def load_spec(path: Path) -> dict:
    spec = read_json(path)
    if not isinstance(spec, dict) or spec.get("frictional_probe_spec") != SPEC_VERSION:
        raise ProbeError("Not a Frictional probe spec (version 1).")
    if spec.get("status") == "frozen":
        frozen = spec.get("frozen")
        if not isinstance(frozen, dict) or frozen.get("sha256") != spec_digest(spec):
            raise ProbeError("The frozen spec has been changed since it was frozen (its SHA-256 no longer matches).")
    return spec


ITEM_KEYS = {
    "predict": {"id", "kind", "form", "function", "call", "input_origin", "key", "key_call", "difficulty",
                "timebox_minutes"},
    "bug": {"id", "kind", "form", "function", "variant", "variant_sha256", "mutation", "witness", "key_calls",
            "hints", "ladder_minutes", "difficulty"},
    "transfer-near": {"id", "kind", "form", "function", "frame", "text", "text_origin",
                      "approved_by_instructor", "reference", "difficulty", "timebox_minutes"},
}
ITEM_KEYS["transfer-far"] = ITEM_KEYS["transfer-near"] | {"barnett_ceci"}
REFERENCE_KEYS = {"path", "function", "calls", "keys", "validated", "key_run"}


def check_items(spec: dict, probes_dir: Path, *, final: bool) -> list[str]:
    """Problems that block freezing (final) or the reference run (not final)."""
    problems = []
    fids = {f["id"] for f in spec.get("functions", [])}
    seen = set()
    for item in spec.get("items", []):
        if not isinstance(item, dict) or item.get("kind") not in ITEM_KEYS:
            problems.append("an item has an unknown kind")
            continue
        iid = item.get("id")
        if set(item) != ITEM_KEYS[item["kind"]]:
            problems.append(f"{iid}: unexpected or missing fields "
                            f"({', '.join(sorted(set(item) ^ ITEM_KEYS[item['kind']]))})")
            continue
        if iid in seen or item["function"] not in fids:
            problems.append(f"{iid}: repeated id or unknown function")
        seen.add(iid)
        if final and item["difficulty"] not in DIFFICULTY:
            problems.append(f"{iid}: difficulty must be low, medium or high")
        if item["kind"] == "bug":
            hints = item["hints"]
            if not (isinstance(hints, dict) and set(hints) == {"partial", "full"}
                    and all(isinstance(h, str) and 0 < len(h) <= 300 for h in hints.values())):
                problems.append(f"{iid}: hints must be a partial and a full hint of at most 300 characters")
            try:
                text = pe.read_bounded(probes_dir, item["variant"]).decode("utf-8")
                if _sha(text) != item["variant_sha256"]:
                    problems.append(f"{iid}: the variant file has changed")
            except (OSError, ValueError, UnicodeError):
                problems.append(f"{iid}: the variant file is missing")
        if item["kind"].startswith("transfer"):
            ref = item["reference"]
            if not isinstance(ref, dict) or not set(ref) <= REFERENCE_KEYS:
                problems.append(f"{iid}: reference has unexpected fields")
                continue
            if not final:
                continue
            text = item["text"]
            if not (isinstance(text, str) and 20 <= len(text.strip()) <= 2000):
                problems.append(f"{iid}: write the task text (20 to 2000 characters)")
            origin = item["text_origin"]
            if not (isinstance(origin, str) and TEXT_ORIGIN.fullmatch(origin)):
                problems.append(f"{iid}: text_origin must be \"instructor\" or \"agent-draft:MODEL\"")
            elif origin != "instructor" and item["approved_by_instructor"] is not True:
                problems.append(f"{iid}: an agent draft needs approved_by_instructor true")
            if ref.get("validated") is not True:
                problems.append(f"{iid}: the reference solution has not passed `probe references`")
            if item["kind"] == "transfer-far" and item["barnett_ceci"] not in BARNETT_CECI:
                problems.append(f"{iid}: barnett_ceci must be one of {', '.join(BARNETT_CECI)}")
    if final and not any(i.get("kind") == "predict" for i in spec.get("items", []) if isinstance(i, dict)):
        problems.append("the probe has no predict items")
    return problems


def verify_keys(spec: dict, probes_dir: Path) -> list[str]:
    """Every stored key must match what the container logs show (so hand edits cannot invent one)."""
    problems = []
    run = spec.get("key_run")
    if not isinstance(run, dict):
        return ["there is no key run"]
    wanted = {}
    for item in spec["items"]:
        if item["kind"] == "predict":
            wanted[item["key_call"]] = (item["id"], item["key"])
        elif item["kind"] == "bug":
            wanted[item["key_calls"]["original"]] = (item["id"], item["witness"]["original"])
            wanted[item["key_calls"]["variant"]] = (item["id"], item["witness"]["variant"])
    if not all(isinstance(c, str) and CALL_ID.fullmatch(c) for c in wanted):
        return ["a key_call is malformed"]
    results, _ = observed(probes_dir, run, set(wanted))
    for cid, (iid, key) in wanted.items():
        if results.get(cid) != key:
            problems.append(f"{iid}: its key does not match the container logs")
    for item in spec["items"]:
        if item["kind"].startswith("transfer") and item["reference"].get("validated"):
            ref = item["reference"]
            if not isinstance(ref.get("key_run"), dict) or not ref["key_run"].get("dir"):
                problems.append(f"{item['id']}: its reference checks have no container run record")
                continue
            ref_results, _ = observed(probes_dir, ref["key_run"], {k["id"] for k in ref["keys"]})
            if any(ref_results.get(k["id"]) != k["result"] for k in ref["keys"]):
                problems.append(f"{item['id']}: its reference checks do not match the container logs")
    return problems


# --------------------------------------------------------------------------
# references and freeze


def references(out: Path, target: Path, run_spec: Path | None = None, runtime=None, echo=print) -> dict:
    import container_run as cr
    probes_dir = _probes_dir(out)
    spec_path = probes_dir / SPEC_FILE
    spec = load_spec(spec_path)
    if spec["status"] != "draft":
        raise ProbeError("Reference solutions are checked on a keyed draft (status \"draft\").")
    problems = check_items(spec, probes_dir, final=False)
    if problems:
        raise ProbeError("Fix the spec first: " + "; ".join(problems))
    root = target.expanduser().resolve(strict=True)
    if _within(probes_dir, root):
        raise ProbeError("The probes folder must be outside the project.")
    try:
        revision = pe.git_head(root).decode("ascii")
    except (OSError, ValueError):
        revision = None
    if revision != spec["target"]["revision"]:
        echo("frictional: note: the project's revision differs from the one the probe was made from.")
    calls, staged, plan = [], {}, []
    for item in spec["items"]:
        if not item["kind"].startswith("transfer"):
            continue
        ref = item["reference"]
        if ref.get("path") is None:
            continue
        rel = ref["path"]
        if not (isinstance(rel, str) and re.fullmatch(r"references/[A-Za-z0-9_-]{1,64}\.py", rel)):
            raise ProbeError(f"{item['id']}: reference.path must look like references/near_1.py.")
        if not (isinstance(ref.get("function"), str) and ref["function"].isidentifier()):
            raise ProbeError(f"{item['id']}: reference.function must name a function.")
        rows = ref.get("calls")
        if not isinstance(rows, list) or not 2 <= len(rows) <= 10:
            raise ProbeError(f"{item['id']}: reference.calls must list 2 to 10 calls.")
        text = pe.read_bounded(probes_dir, rel).decode("utf-8", "strict")
        if _parse(text, rel) is None:
            raise ProbeError(f"{item['id']}: {rel} does not parse as Python.")
        staged[rel] = text
        ids = []
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"args", "kwargs", "expect"} or "args" not in row:
                raise ProbeError(f"{item['id']}: each reference call is {{\"args\", optional \"kwargs\", \"expect\"}}.")
            args = repr(_literal(row["args"], tuple))
            kwargs = repr(_literal(row.get("kwargs", "{}"), dict))
            cid = f"c{len(calls) + 1}"
            calls.append({"id": cid, "module": PurePosixPath(rel).name.replace("-", "_"),
                          "function": ref["function"], "args": args, "kwargs": kwargs,
                          "file": f".frictional-probe/{rel}"})
            ids.append((cid, args, kwargs, row.get("expect")))
        plan.append((item, ids))
    if not calls:
        raise ProbeError("No transfer item has a reference solution yet (reference.path).")
    runtime = runtime if runtime is not None else cr.find_runtime()
    if runtime is None:
        raise ProbeError("No working Docker or Podman was found; reference solutions run only in a container.")
    files, _ = pe.inventory(root)
    run_spec_value, _ = resolve_run_spec(root, files, run_spec)
    if run_spec_value is None:
        raise ProbeError("No run spec, frictional-run.json or Python manifest was found.")
    n = 1
    while os.path.lexists(probes_dir / f"references-run-{n}"):
        n += 1
    run_dir = probes_dir / f"references-run-{n}"
    run_dir.mkdir(mode=0o700)
    echo(f"frictional: running {len(calls)} reference checks in a container...")
    record = key_run(root, run_spec_value, calls, staged, run_dir, revision, runtime)
    record.update(dir=f"references-run-{n}", receipt=f"references-run-{n}/run/receipt.json")
    results, notes = observed(probes_dir, record, {c["id"] for c in calls})
    for note in notes:
        echo(f"  note: {note}")
    summary = {}
    for item, ids in plan:
        keys, ok = [], True
        name = item["reference"]["function"]
        for cid, args, kwargs, expect in ids:
            result = results.get(cid)
            call = _call_text(name, {"args": args, "kwargs": kwargs})
            if result is None:
                ok = False
                continue
            keys.append({"id": cid, "call": call, "result": result})
            if expect is not None and (result["status"] != "value" or result["repr"] != expect):
                ok = False
                echo(f"  {item['id']}: {call} returned {show(result)}, expected {expect}")
        item["reference"].update(keys=keys, validated=ok and len(keys) == len(ids), key_run=record)
        summary[item["id"]] = item["reference"]["validated"]
    _replace(probes_dir / SPEC_FILE, json.dumps(spec, indent=2) + "\n")
    render(probes_dir, spec)
    for iid, ok in summary.items():
        echo(f"frictional: {iid}: reference checks {'passed' if ok else 'did not all pass'}.")
    return summary


def freeze(out: Path, echo=print) -> str:
    probes_dir = _probes_dir(out)
    spec_path = probes_dir / SPEC_FILE
    spec = load_spec(spec_path)
    if spec["status"] == "frozen":
        raise ProbeError("This probe is already frozen.")
    if spec["status"] != "draft":
        raise ProbeError("Only a keyed draft can be frozen; this spec has no keys (run make with --run).")
    problems = check_items(spec, probes_dir, final=True)
    if not problems:
        problems = verify_keys(spec, probes_dir)
    if problems:
        raise ProbeError("Cannot freeze yet:\n  - " + "\n  - ".join(problems))
    spec["status"] = "frozen"
    spec["frozen"] = {"at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    spec["frozen"]["sha256"] = spec_digest(spec)
    _replace(spec_path, json.dumps(spec, indent=2) + "\n")
    render(probes_dir, spec)
    echo(f"frictional: froze {spec_path} (SHA-256 {spec['frozen']['sha256']}); receipt templates written.")
    return spec["frozen"]["sha256"]


# --------------------------------------------------------------------------
# Receipts -> evidence packets


def _bool(value, what):
    if type(value) is not bool:
        raise ProbeError(f"{what} must be true or false.")
    return value


def _time(value, what):
    try:
        return pe.timestamp(value)
    except ValueError:
        raise ProbeError(f"{what} must be an ISO time with a timezone, like 2027-02-18T10:05:00-05:00.") from None


def check_receipt(receipt, spec: dict) -> dict:
    if not isinstance(receipt, dict) or set(receipt) != {"frictional_probe_receipt", "spec_sha256", "session", "items"}:
        raise ProbeError("A receipt has exactly frictional_probe_receipt, spec_sha256, session and items.")
    if receipt["frictional_probe_receipt"] != RECEIPT_VERSION:
        raise ProbeError("Unsupported receipt version.")
    if receipt["spec_sha256"] != spec["frozen"]["sha256"]:
        raise ProbeError("The receipt cites a different spec (spec_sha256 does not match).")
    session = receipt["session"]
    if not isinstance(session, dict) or set(session) != {"kind", "form", "started_at", "conditions"}:
        raise ProbeError("session has exactly kind, form, started_at and conditions.")
    if (session["kind"], session["form"]) not in (("initial", "A"), ("reprobe", "B")):
        raise ProbeError("An initial session uses form A and a re-probe uses form B.")
    started = _time(session["started_at"], "session.started_at")
    cond = session["conditions"]
    if not isinstance(cond, dict) or set(cond) != {"ai", "notes", "proctored"}:
        raise ProbeError("session.conditions has exactly ai, notes and proctored.")
    if _bool(cond["ai"], "conditions.ai") or _bool(cond["notes"], "conditions.notes"):
        raise ProbeError("Probe receipts are for sessions without AI or notes, as the probe was built for.")
    _bool(cond["proctored"], "conditions.proctored")
    expected = {i["id"]: i for i in spec["items"] if i["form"] == session["form"]}
    rows = receipt["items"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ProbeError(f"The receipt must list every item of form {session['form']} once.")
    clean, intervals = [], []
    for row in rows:
        if not isinstance(row, dict) or row.get("item") not in expected:
            raise ProbeError("A receipt row names an item that is not in this form.")
        item = expected.pop(row["item"])
        attempted = _bool(row.get("attempted"), f"{item['id']}: attempted")
        outcome = "solved_at" if item["kind"] == "bug" else "correct"
        allowed = {"item", "attempted"} | ({outcome, "confidence", "started_at", "ended_at"} if attempted else set())
        if not set(row) <= allowed or (attempted and outcome not in row):
            raise ProbeError(f"{item['id']}: fields must be item, attempted"
                             + (f", {outcome}, and optional confidence, started_at, ended_at." if attempted else " only (not attempted)."))
        entry = {"item": item, "attempted": attempted, "confidence": None, "start": None, "end": None}
        if attempted:
            if outcome == "solved_at":
                if row["solved_at"] not in SOLVED_AT:
                    raise ProbeError(f"{item['id']}: solved_at must be none, partial, full or unsolved.")
                entry["solved_at"] = row["solved_at"]
            else:
                entry["correct"] = _bool(row["correct"], f"{item['id']}: correct")
            if "confidence" in row:
                if type(row["confidence"]) not in (int, float) or row["confidence"] not in CONFIDENCE:
                    raise ProbeError(f"{item['id']}: confidence must be 0, 0.25, 0.5, 0.75 or 1.")
                entry["confidence"] = float(row["confidence"])
            if ("started_at" in row) != ("ended_at" in row):
                raise ProbeError(f"{item['id']}: give both started_at and ended_at, or neither.")
            if "started_at" in row:
                a, b = _time(row["started_at"], f"{item['id']}: started_at"), _time(row["ended_at"], f"{item['id']}: ended_at")
                if b < a or a < started:
                    raise ProbeError(f"{item['id']}: the item must end after it starts, and start after the session.")
                entry.update(start=row["started_at"], end=row["ended_at"])
                intervals.append((a, b))
        clean.append(entry)
    intervals.sort()
    if any(x[1] > y[0] for x, y in zip(intervals, intervals[1:])):
        raise ProbeError("Item times overlap; a probe is answered one item at a time.")
    return {"kind": session["kind"], "form": session["form"], "started": started, "rows": clean}


def receipts_to_packets(spec: dict, receipts: list) -> tuple[dict, list[str]]:
    """Validated receipts -> {packet: value} for the existing --evidence dimensions, plus notes."""
    if spec.get("status") != "frozen":
        raise ProbeError("Receipts are read against a frozen spec only.")
    if not 1 <= len(receipts) <= 2:
        raise ProbeError("Give one initial receipt and at most one re-probe receipt, for one student.")
    sessions = [check_receipt(r, spec) for r in receipts]
    if len({s["kind"] for s in sessions}) != len(sessions):
        raise ProbeError("Each session kind may appear once.")
    options = getattr(pe, "PACKET_OPTIONS", {})
    calibration, transfer, retrieval, ladder, timed, notes = [], [], [], [], [], []
    deadline = spec["target"].get("deadline")
    deadline_at = pe.timestamp(deadline) if deadline else None
    for s in sessions:
        delay = None
        if deadline_at is not None:
            delay = (s["started"] - deadline_at).total_seconds() / 3600
        for row in s["rows"]:
            item = row["item"]
            if not row["attempted"]:
                continue
            kind = item["kind"]
            unaided = row["solved_at"] == "none" if kind == "bug" else row["correct"]
            if row["confidence"] is not None:
                calibration.append({"confidence": row["confidence"], "correct": unaided})
            if kind.startswith("transfer"):
                transfer.append({"distance": "near" if kind == "transfer-near" else "far", "correct": row["correct"]})
            if kind == "predict" and delay is not None and 0 <= delay <= 87600:
                retrieval.append({"delay_hours": round(delay, 2), "correct": row["correct"]})
            if kind == "bug":
                reached = {"none": ["none"], "partial": ["none", "hint"], "full": ["none", "hint", "worked"],
                           "unsolved": ["none", "hint", "worked"]}[row["solved_at"]]
                for n, level in enumerate(reached):
                    ladder.append({"level": level, "successful": row["solved_at"] != "unsolved" and n == len(reached) - 1})
            if row["start"] is not None:
                timed.append({"start": row["start"], "end": row["end"], "difficulty": item["difficulty"]})
    packets = {}
    if calibration:
        packets["calibration"] = {"observations": calibration}
    if transfer:
        packets["transfer"] = {"observations": transfer}
    if deadline_at is None:
        notes.append("retrieval: not written, because the spec has no deadline to measure delay from.")
    elif any(s["started"] < deadline_at for s in sessions):
        notes.append("retrieval: rows from a session before the deadline were left out.")
    if retrieval:
        packets["retrieval"] = {"observations": retrieval}
    if ladder:
        if "ladder" in options.get("scaffolding", {}).get("design", ()):
            packets["scaffolding"] = {"observations": ladder, "design": "ladder"}
        else:
            notes.append("scaffolding: not written, because this Frictional's scaffolding adapter cannot yet "
                         "report hint-ladder data without the preprint's ratio.")
    if timed:
        if "item" in options.get("temporal-engagement", {}).get("grain", ()):
            packets["temporal-engagement"] = {"milestones": timed, "grain": "item"}
        else:
            notes.append("temporal-engagement: not written, because this Frictional's adapter would describe "
                         "item times as milestone intervals.")
    for name, value in packets.items():
        result = pe.structured(name, value)
        if result.get("status") != "reported":
            raise ProbeError(f"Internal check failed: the {name} packet was not accepted.")
    return packets, notes


def convert(spec_path: Path, receipt_paths: list[Path], out: Path, merge: Path | None = None, echo=print) -> dict:
    spec = load_spec(spec_path)
    if out.expanduser().exists() or out.expanduser().is_symlink():
        raise ProbeError(f"{out} already exists; choose a new file.")
    packets, notes = receipts_to_packets(spec, [read_json(p) for p in receipt_paths])
    combined = {}
    if merge is not None:
        combined = read_json(merge)
        if not isinstance(combined, dict):
            raise ProbeError("--merge must be an evidence JSON object.")
        clash = set(combined) & set(packets)
        if clash:
            raise ProbeError(f"--merge already has {', '.join(sorted(clash))}; refusing to replace it.")
    combined.update(packets)
    _write_new(out.expanduser(), json.dumps(combined, indent=2) + "\n")
    for name, value in packets.items():
        rows = value.get("observations") or value.get("milestones")
        echo(f"frictional: {name}: {len(rows)} row(s)")
    for note in notes:
        echo(f"  note: {note}")
    echo(f"frictional: wrote {out}. Pass it to `frictional audit TARGET --evidence {out}`.")
    return combined


# --------------------------------------------------------------------------
# Command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="frictional probe",
                                     description="In-class comprehension probes built from a project's own code.")
    sub = parser.add_subparsers(dest="action", required=True)
    mk = sub.add_parser("make", help="choose functions, compute keys in a container, write the probe")
    mk.add_argument("target", type=Path)
    mk.add_argument("--starter", type=Path, required=True, help="the course starter folder (required)")
    mk.add_argument("--out", type=Path, required=True, help="output folder; the probe goes in OUT/probes/")
    mk.add_argument("--run", action="store_true", help="compute keys in a Docker or Podman container")
    mk.add_argument("--run-spec", type=Path, help="run spec JSON for setup (default: the project's own)")
    mk.add_argument("--count", type=int, default=DEFAULT_FUNCTIONS, help="functions, 3 to 5 (default 4)")
    mk.add_argument("--seed", type=int, default=1, help="tie-break seed recorded in the spec")
    mk.add_argument("--deadline", help="assignment deadline, ISO time with timezone (for retrieval delays)")
    mk.add_argument("--only", action="append", metavar="NAME",
                    help="a function the student nominated (NAME or path.py:NAME); repeat for more")
    mk.add_argument("--inputs", type=Path, help="extra inputs JSON written by the instructor")
    ref = sub.add_parser("references", help="run the transfer tasks' reference solutions in a container")
    ref.add_argument("out", type=Path)
    ref.add_argument("--target", type=Path, required=True)
    ref.add_argument("--run-spec", type=Path)
    fr = sub.add_parser("freeze", help="check and freeze the probe; writes receipt templates")
    fr.add_argument("out", type=Path)
    rc = sub.add_parser("receipts", help="turn receipts into an --evidence file")
    rc.add_argument("receipts", type=Path, nargs="+")
    rc.add_argument("--spec", type=Path, required=True)
    rc.add_argument("--out", type=Path, required=True)
    rc.add_argument("--merge", type=Path, help="existing evidence JSON to add the packets to")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "make":
            if args.run_spec and not args.run:
                raise ProbeError("--run-spec needs --run.")
            make(args.target, args.starter, args.out, run=args.run, run_spec=args.run_spec, count=args.count,
                 seed=args.seed, deadline=args.deadline, nominated=args.only, inputs_file=args.inputs)
        elif args.action == "references":
            references(args.out, args.target, args.run_spec)
        elif args.action == "freeze":
            freeze(args.out)
        else:
            convert(args.spec, args.receipts, args.out, args.merge)
        return 0
    except (ProbeError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Cannot access an input or output."
        print(f"frictional probe: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
