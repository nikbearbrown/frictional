"""Opt-in container run: install and run a project in a fresh Linux container.

Frictional is otherwise read-only. This module runs only when the owner asks
for it, and project code never runs on the host: the working tree is copied
(never mounted) into a new container with no bind mounts and no runtime
socket, every command runs through `<runtime> exec ... sh -c CMD` with an
argument list, and the container is removed in a `finally` block.

Run spec (a JSON object; unknown keys and malformed values fail closed):

    {"image": "python:3.12-slim",
     "setup": [{"cmd": "python -m pip install -r requirements.txt", "source": "README.md:12"}],
     "run":   [{"cmd": "python app.py --demo", "source": "README.md:20"}],
     "test":  [{"cmd": "python -m pytest -q", "source": "README.md:31"}],
     "serve": {"cmd": "python app.py", "port": 8000, "path": "/", "source": "README.md:25"},
     "timeout_seconds": 600,
     "network": "setup"}

`timeout_seconds` is per command (1..3600, default 600). `network` is "setup"
(default: on for setup, disconnected and checked before anything else),
"none" (never) or "all" (always; required when the spec has "serve").

run_project() writes OUT/run/: receipt.json, transcript.jsonl and
logs/<phase>-<n>.log (history: logs/history-<order>-<n>.log). Logs and
transcript lines are masked for likely secrets. run_results() turns a receipt
into Frictional dimension results that contain only fixed phrases, counts,
outcomes, seconds, image names and command sources, never command output.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

import process_evidence as pe

RUN_DIMENSIONS = ("install-test", "run-check", "test-history", "serve-check")
PHASES = ("setup", "run", "test")
SPEC_KEYS = frozenset({"image", "setup", "run", "test", "serve", "timeout_seconds", "network"})
NETWORK_POLICIES = ("setup", "none", "all")
MAX_SPEC_BYTES = 65536
MAX_COMMANDS = 20
MAX_CMD = 2000
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600
MAX_HISTORY = 250
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_COPY_FILES = 20000
MAX_COPY_BYTES = 1024 * 1024 * 1024
MAX_COPY_DEPTH = 32
MAX_LOG = 256 * 1024
MAX_LINE = 4096
MAX_TRANSCRIPT = 5000
MAX_CONTROL_OUTPUT = 1024 * 1024
SERVE_WAIT = 60
MAX_HOLD = 3600
CONTROL_TIMEOUT = 120
COPY_TIMEOUT = 900
PULL_TIMEOUT = 1800
INFO_TIMEOUT = 20
KEEPALIVE = "604800"
WORKDIR = "/work"
HISTORY_ROOT = "/history"
LABEL = "frictional=1"
MEMORY = "2g"
CPUS = 2
PIDS = 512
CAPS = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID")
ENV = {"CI": "true", "PYTHONUNBUFFERED": "1", "PIP_NO_INPUT": "1",
       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "NPM_CONFIG_UPDATE_NOTIFIER": "false"}
LOCATOR = "run/receipt.json"

IMAGE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*){0,4}"
                   r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[a-f0-9]{64})?")
SOURCE = re.compile(r"[A-Za-z0-9._/#@(\[-][A-Za-z0-9 ._/:;#@+,()\[\]=-]{0,199}")
HTTP_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,199}")
SHA = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_~-]{0,63}")
DIGEST = re.compile(r"[a-z0-9][a-z0-9._/-]{0,254}@sha256:[a-f0-9]{64}")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}")
NETWORK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
LOG_PATH = re.compile(r"run/logs/[a-z0-9-]{1,64}\.log")
OUTCOMES = ("passed", "failed", "not_run")

# Never copied into the container: files that are almost always secrets.
CREDENTIAL_FILE = re.compile(
    r"\.env(?:\.(?!(?:example|sample|template|dist|defaults?)$)[A-Za-z0-9_.-]+)?|"
    r".+\.(?:pem|key|p12|pfx|jks|keystore)|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|"
    r"\.netrc|\.pypirc|credentials\.json|service[-_]account[A-Za-z0-9_.-]*\.json", re.I)

ERRORS = {
    "image-unavailable": "the image could not be found or pulled",
    "create-failed": "the container could not be created",
    "copy-failed": "the project copy could not be placed in the container",
    "start-failed": "the container could not be started",
}
STOPS = {
    "setup-failed": "setup failed",
    "network-not-off": "the network could not be confirmed off after setup",
    "interrupted": "the run was interrupted",
}

ALT_CLEAN = ("A clean container differs from the author's machine; a failure can come from "
             "undocumented prerequisites rather than the code.")
ALT_INSTALL = ("A completed setup shows the declared dependencies resolved in this run, "
               "not that the project works.")
ALT_DEFAULT = ("Some commands were inferred by the tool from manifest files, not taken "
               "from the project's own instructions.")
ALT_NETWORK = ("Network access during these commands lets results depend on remote package "
               "versions available at run time.")
ALT_EXIT = ("An exit status of 0 shows a command finished without reporting an error, "
            "not that its output is correct.")
ALT_TESTS = "Tests that pass may not exercise the main path."
ALT_HISTORY = "Older commits were tested with dependencies installed for the current revision."
ALT_ORDER = ("Commit order is as supplied; rewritten history or changed test commands can alter "
             "the sequence, and it does not measure learning or effort.")
ALT_SERVE = ("An HTTP answer shows that something listened on the port, not that the "
             "application's features work.")
ALT_SERVE_FAIL = ("A server can need more start-up time, configuration or data than a clean "
                  "container provides.")


# --------------------------------------------------------------------------
# Masking and failure categories


_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk)-(?:[A-Za-z0-9]+-)*[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNMENT = re.compile(
    r"(?i)((?<![A-Za-z0-9])[A-Za-z0-9_.-]*?"
    r"(?:password|passwd|passphrase|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|auth)"
    r"(?![a-z])[A-Za-z0-9_.-]*[\"']?\s*[=:]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;'\"]+)")
_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


def _mask_line(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[masked]", text)
    text = _URL_CREDENTIALS.sub(r"\1[masked]@", text)
    text = _BEARER.sub(lambda m: m[1] + " [masked]", text)
    return _ASSIGNMENT.sub(lambda m: m[1] + "[masked]", text)


class _Masker:
    """Line-by-line masking that also hides multi-line private key blocks."""

    def __init__(self):
        self.in_key = False

    def line(self, text: str) -> str:
        if self.in_key:
            self.in_key = not _KEY_END.search(text)
            return "[masked]"
        if _KEY_BEGIN.search(text):
            self.in_key = not _KEY_END.search(text)
            return "[masked]"
        return _mask_line(text)


def mask_secrets(text: str) -> str:
    """Mask likely secrets (API tokens, AWS keys, key=value secrets, URL passwords, keys)."""
    masker = _Masker()
    return "\n".join(masker.line(line) for line in text.split("\n"))


_CATEGORY_RULES = (
    ("syntax", re.compile(r"\b(?:SyntaxError|IndentationError|TabError)\b")),
    ("dependency", re.compile(r"\b(?:ModuleNotFoundError|ImportError)\b|No module named|"
                              r"Cannot find module|Cannot find package|command not found|: not found\b")),
    ("build", re.compile(r"\berror TS\d+|\berror\[E\d+\]|\bBUILD FAILED\b|\bBUILD FAILURE\b|"
                         r"\bCompilation failed\b|compilation terminated")),
    ("assertion", re.compile(r"\bAssertionError\b|\bFAILED\b|\bfailing\b|\b\d+ failed\b")),
    ("build", re.compile(r"\berror: ")),
)


def failure_category(text: str, timed_out: bool = False) -> str:
    """Simple, ordered regex categories for a failed command's output."""
    if timed_out:
        return "timeout"
    for name, pattern in _CATEGORY_RULES:
        if pattern.search(text):
            return name
    return "other"


# --------------------------------------------------------------------------
# Spec validation and the conservative default spec


def _commands(value, key):
    if not isinstance(value, list) or len(value) > MAX_COMMANDS:
        raise ValueError(f"Run spec '{key}' must be a list of at most {MAX_COMMANDS} commands.")
    out = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"cmd", "source"}:
            raise ValueError(f"Each '{key}' command must be an object with exactly 'cmd' and 'source'.")
        out.append({"cmd": _cmd(item["cmd"], key), "source": _source(item["source"], key)})
    return out


def _cmd(value, key):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_CMD
            or "\0" in value):
        raise ValueError(f"A '{key}' cmd must be non-empty text of at most {MAX_CMD} characters with no NUL.")
    return value


def _source(value, key):
    if not isinstance(value, str) or not SOURCE.fullmatch(value) or value != value.strip():
        raise ValueError(f"A '{key}' source must be a short locator such as README.md:12 "
                         "(at most 200 plain characters).")
    return value


def _integer(value, low, high, message):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(message)
    return value


def check_spec(value) -> dict:
    """Validate a run spec and return a normalized copy (all keys present)."""
    if not isinstance(value, dict):
        raise ValueError("Run spec must be a JSON object.")
    if not set(value) <= SPEC_KEYS:
        raise ValueError("Run spec has an unknown key; allowed keys are image, setup, run, test, "
                         "serve, timeout_seconds and network.")
    image = value.get("image")
    if not isinstance(image, str) or len(image) > 255 or not IMAGE.fullmatch(image):
        raise ValueError("Run spec 'image' is required and must look like name[:tag][@sha256:digest], "
                         "for example python:3.12-slim.")
    spec = {"image": image}
    for key in PHASES:
        spec[key] = _commands(value.get(key, []), key)
    serve = value.get("serve")
    if serve is not None:
        if (not isinstance(serve, dict) or not {"cmd", "port", "source"} <= set(serve)
                or not set(serve) <= {"cmd", "port", "path", "source"}):
            raise ValueError("Run spec 'serve' must be an object with 'cmd', 'port', 'source' and optional 'path'.")
        path = serve.get("path", "/")
        if not isinstance(path, str) or not HTTP_PATH.fullmatch(path):
            raise ValueError("Run spec 'serve' path must start with / and use plain URL characters.")
        serve = {"cmd": _cmd(serve["cmd"], "serve"),
                 "port": _integer(serve["port"], 1, 65535, "Run spec 'serve' port must be an integer 1 to 65535."),
                 "path": path, "source": _source(serve["source"], "serve")}
    spec["serve"] = serve
    spec["timeout_seconds"] = _integer(value.get("timeout_seconds", DEFAULT_TIMEOUT), 1, MAX_TIMEOUT,
                                       "Run spec 'timeout_seconds' must be an integer 1 to 3600.")
    network = value.get("network", "setup")
    if network not in NETWORK_POLICIES:
        raise ValueError("Run spec 'network' must be \"setup\", \"none\" or \"all\".")
    spec["network"] = network
    if serve is not None and network != "all":
        raise ValueError("A run spec with 'serve' needs \"network\": \"all\" so the port can be published.")
    if not any(spec[key] for key in PHASES) and serve is None:
        raise ValueError("Run spec lists no setup, run, test or serve commands.")
    return spec


def load_spec(path: Path) -> dict:
    """Read a bounded run spec JSON file (no symlink, no repeated keys) and validate it."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with os.fdopen(os.open(Path(path).expanduser(), flags), "rb") as source:
            meta = os.fstat(source.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > MAX_SPEC_BYTES:
                raise ValueError("Run spec must be a regular JSON file no larger than 64 KiB.")
            payload = source.read(MAX_SPEC_BYTES + 1)
    except OSError as exc:
        raise ValueError("Cannot read the run spec as a regular file (symlinks are refused).") from exc
    if len(payload) > MAX_SPEC_BYTES:
        raise ValueError("Run spec must be a regular JSON file no larger than 64 KiB.")

    def reject_constant(_value):
        raise ValueError("Run spec contains a non-finite number.")

    def distinct(pairs):
        out = {}
        for key, item in pairs:
            if key in out:
                raise ValueError("Run spec repeats a key.")
            out[key] = item
        return out

    try:
        value = json.loads(payload.decode("utf-8"), parse_constant=reject_constant,
                           object_pairs_hook=distinct)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Run spec is not valid UTF-8 JSON.") from exc
    return check_spec(value)


def _read_text(root, name):
    try:
        return pe.read_bounded(root, name).decode("utf-8", "strict")
    except (OSError, ValueError, UnicodeError):
        return None


_PYTEST_NAME = r"pytest(?:[-_.][A-Za-z0-9]+)*"


def _requirements_pytest(root, top, name="requirements.txt", depth=0):
    text = _read_text(root, name)
    if text is None:
        return False
    if re.search(rf"(?im)^\s*{_PYTEST_NAME}\s*(?:[<>=!~;\[@]|$)", text):
        return True
    if depth == 0:
        for include in re.findall(r"(?m)^\s*(?:-r|--requirement)\s*=?\s*(\S+)\s*$", text):
            if include in top and include != name and _requirements_pytest(root, top, include, 1):
                return True
    return False


def _pyproject_pytest(root):
    text = _read_text(root, "pyproject.toml")
    if text is None:
        return False
    pattern = re.compile(rf"\s*{_PYTEST_NAME}\s*(?:[<>=!~;\[(]|$)", re.I)
    if tomllib is None:
        return bool(re.search(rf"[\"']{_PYTEST_NAME}\s*(?:[<>=!~;\[]|[\"'])", text, re.I))
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        return False
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    deps = project.get("dependencies") if isinstance(project.get("dependencies"), list) else []
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    poetry_deps = poetry.get("dependencies") if isinstance(poetry.get("dependencies"), dict) else {}
    names = [d for d in deps if isinstance(d, str)] + [k for k in poetry_deps if isinstance(k, str)]
    return any(pattern.match(name) for name in names)


def _npm_test(root):
    text = _read_text(root, "package.json")
    if text is None:
        return False
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    test = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(test, str) and bool(test.strip()) and "no test specified" not in test


def default_spec(root: Path, files: dict[str, int]) -> dict | None:
    """Conservative spec from root-level manifests; never guesses run or serve commands.

    Python manifests win when a root also has package.json. Every command's
    source starts with "default:" so a report can say the tool guessed it.
    """
    root = Path(root)
    top = {name for name in files if "/" not in name}
    if "requirements.txt" in top or "pyproject.toml" in top:
        if "requirements.txt" in top:
            setup = {"cmd": "python -m pip install --no-cache-dir -r requirements.txt",
                     "source": "default: requirements.txt"}
            pytest, where = _requirements_pytest(root, top), "requirements.txt"
        else:
            setup = {"cmd": "python -m pip install --no-cache-dir .", "source": "default: pyproject.toml"}
            pytest, where = _pyproject_pytest(root), "pyproject.toml"
        spec = {"image": "python:3.12-slim", "setup": [setup]}
        in_tests = any(name.startswith("tests/") for name in files)
        named = any(re.fullmatch(r"test_[^/]*\.py", PurePosixPath(name).name) for name in files)
        if pytest and (in_tests or named):
            spec["test"] = [{"cmd": "python -m pytest -q", "source": f"default: test files, pytest in {where}"}]
        elif in_tests or named:
            rooted = any(re.fullmatch(r"test_[^/]*\.py", name) for name in top) or "tests/__init__.py" in files
            cmd = "python -m unittest discover -v" if rooted or not in_tests else "python -m unittest discover -s tests -v"
            spec["test"] = [{"cmd": cmd, "source": "default: test files, no pytest declared"}]
    elif "package.json" in top:
        lock = "package-lock.json" in top
        spec = {"image": "node:22-slim",
                "setup": [{"cmd": "npm ci" if lock else "npm install",
                           "source": "default: package-lock.json" if lock else "default: package.json"}]}
        if _npm_test(root):
            spec["test"] = [{"cmd": "npm test", "source": "default: package.json scripts.test"}]
    else:
        return None
    spec["timeout_seconds"] = DEFAULT_TIMEOUT
    spec["network"] = "setup"
    return check_spec(spec)


# --------------------------------------------------------------------------
# Host-side process helpers (runtime CLI only; never project code, never a shell)


def _call(argv, timeout=CONTROL_TIMEOUT, stdin=None):
    """Run one container-runtime control command; return (exit code or None, stdout, stderr)."""
    try:
        done = subprocess.run(argv, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, b"", b""
    return done.returncode, done.stdout[:MAX_CONTROL_OUTPUT], done.stderr[:MAX_CONTROL_OUTPUT]


def _spawn(argv):
    """Start a streamed `<runtime> exec`; its own session so Ctrl-C reaches only the runner."""
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _probe(url):
    """HTTP status from the published local port, or None. No proxy, no redirects, no body kept."""
    try:
        with _OPENER.open(url, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return None


def find_runtime() -> dict | None:
    """Docker, then Podman: the CLI must exist and the daemon must answer within 20 s."""
    for name, fmt in (("docker", "{{.ServerVersion}}"), ("podman", "{{.Version.Version}}")):
        binary = shutil.which(name)
        if not binary:
            continue
        code, out, _ = _call([binary, "info", "--format", fmt], timeout=INFO_TIMEOUT)
        version = out.decode("utf-8", "replace").strip()
        if code == 0 and version:
            return {"name": name, "version": version if VERSION.fullmatch(version) else "unknown"}
    return None


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Copying the working tree (no symlinks, no hard links, no .git, no caches)


def _copy_tree(root: Path, dest: Path) -> dict:
    stats = {"files": 0, "bytes": 0, "symlinks_skipped": 0, "cache_dirs_skipped": 0,
             "credential_like_skipped": 0, "other_skipped": 0}
    too_big = ValueError(f"Project is too large to copy into a container (more than {MAX_COPY_FILES} "
                         "files or 1 GiB after exclusions).")

    def copy_file(parent_fd, name, target):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except OSError:
            stats["other_skipped"] += 1
            return
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                stats["other_skipped"] += 1
                return
            if stats["files"] >= MAX_COPY_FILES or stats["bytes"] + info.st_size > MAX_COPY_BYTES:
                raise too_big
            copied = 0
            with os.fdopen(fd, "rb", closefd=False) as src, open(target, "xb") as dst:
                while copied < info.st_size:
                    chunk = src.read(min(1 << 20, info.st_size - copied))
                    if not chunk:
                        break
                    dst.write(chunk)
                    copied += len(chunk)
            os.chmod(target, 0o755 if info.st_mode & 0o111 else 0o644)
            stats["files"] += 1
            stats["bytes"] += copied
        finally:
            os.close(fd)

    def walk(dir_fd, target, depth):
        entries = []
        with os.scandir(dir_fd) as it:
            for entry in it:
                try:
                    entries.append((entry.name, entry.stat(follow_symlinks=False)))
                except OSError:
                    stats["other_skipped"] += 1
        for name, info in sorted(entries):
            if stat.S_ISLNK(info.st_mode):
                stats["symlinks_skipped"] += 1
            elif stat.S_ISDIR(info.st_mode):
                if name in pe.SKIP:
                    stats["cache_dirs_skipped"] += name != ".git"
                elif depth >= MAX_COPY_DEPTH:
                    stats["other_skipped"] += 1
                else:
                    try:
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
                    except OSError:
                        stats["other_skipped"] += 1
                        continue
                    try:
                        (target / name).mkdir(mode=0o755)
                        walk(child, target / name, depth + 1)
                    finally:
                        os.close(child)
            elif stat.S_ISREG(info.st_mode):
                if CREDENTIAL_FILE.fullmatch(name):
                    stats["credential_like_skipped"] += 1
                else:
                    copy_file(dir_fd, name, target / name)
            else:
                stats["other_skipped"] += 1

    with pe.directory(root) as fd:
        walk(fd, dest, 0)
    return stats


# --------------------------------------------------------------------------
# Streaming execution


class _Transcript:
    def __init__(self, path, start):
        self.start = start
        self.lock = threading.Lock()
        self.count = 0
        self.dropped = 0
        self.handle = open(path, "x", encoding="utf-8")

    def now(self):
        return round(time.monotonic() - self.start, 3)

    def add(self, t, phase, n, stream, line, order=None):
        row = {"t": t, "phase": phase, "n": n, "stream": stream, "line": line}
        if order is not None:
            row["order"] = order
        with self.lock:
            if self.count >= MAX_TRANSCRIPT or self.handle.closed:
                self.dropped += 1
                return
            self.handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.count += 1

    def close(self):
        with self.lock:
            if not self.handle.closed:
                self.handle.close()


class _Exec:
    """One `<runtime> exec`; output lines are masked and timed as they arrive."""

    def __init__(self, argv, transcript, phase, n, order=None):
        self.transcript, self.phase, self.n, self.order = transcript, phase, n, order
        self.lock = threading.Lock()
        self.log = bytearray()
        self.omitted = 0
        self.t_start = transcript.now()
        self.t_end = None
        self.proc = _spawn(argv)
        self.readers = [threading.Thread(target=self._read, args=(pipe, stream), daemon=True)
                        for pipe, stream in ((self.proc.stdout, "stdout"), (self.proc.stderr, "stderr"))]
        for reader in self.readers:
            reader.start()

    def _read(self, pipe, stream):
        masker = _Masker()
        try:
            for raw in iter(lambda: pipe.readline(MAX_LINE), b""):
                t = self.transcript.now()
                line = masker.line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                data = (line + "\n").encode("utf-8")
                with self.lock:
                    if not self.omitted and len(self.log) + len(data) <= MAX_LOG:
                        self.log += data
                    else:
                        self.omitted += len(data)
                self.transcript.add(t, self.phase, self.n, stream, line, self.order)
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    def running(self):
        return self.proc.poll() is None

    def _finish(self):
        for reader in self.readers:
            reader.join(10)
        self.t_end = self.transcript.now()

    def wait(self, timeout):
        try:
            code = self.proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            code, timed_out = None, True
        self._finish()
        return code, timed_out

    def stop(self):
        if self.running():
            self.proc.kill()
        self.proc.wait()
        self._finish()

    def text(self):
        with self.lock:
            return self.log.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# The run


class _Runner:
    def __init__(self, binary, runtime, spec, run_dir, copy_dir, copy_stats, history,
                 serve, serve_seconds, revision):
        self.binary, self.spec, self.run_dir = binary, spec, run_dir
        self.copy_dir, self.history = copy_dir, history
        self.serving, self.serve_seconds = serve, serve_seconds
        self.name = "frictional-" + secrets.token_hex(6)
        self.start = time.monotonic()
        self.baseline = None
        self.attempted = False
        tests = bool(spec["test"])
        self.receipt = {
            "runner": "frictional",
            "receipt_version": 1,
            "runtime": {"name": runtime["name"], "version": runtime["version"]},
            "image": spec["image"], "image_digest": None, "image_id": None,
            "image_platform": None, "image_pulled": False,
            "revision": revision,
            "source_tree": "working-tree copy" + (" at a supplied revision" if revision else ""),
            "started_at": _now_iso(), "finished_at": None,
            "network": spec["network"],
            "network_during_setup": "off" if spec["network"] == "none" else "on",
            "network_during_run": "on" if spec["network"] == "all" else "off",
            "network_check": "not-needed" if spec["network"] == "all" else "pending",
            "isolated": True, "environment": "posix-container", "timing": "streamed",
            "container": {"name": self.name, "label": LABEL, "removed": None},
            "limits": {"memory": MEMORY, "cpus": CPUS, "pids": PIDS,
                       "timeout_seconds": spec["timeout_seconds"], "log_bytes": MAX_LOG,
                       "transcript_lines": MAX_TRANSCRIPT, "serve_wait_seconds": SERVE_WAIT},
            "security": {"bind_mounts": False, "runtime_socket": False, "no_new_privileges": True,
                         "cap_drop": "ALL", "cap_add": list(CAPS), "user": "image default",
                         "published_port": "127.0.0.1 random host port" if serve else None},
            "env": dict(ENV),
            "copy": copy_stats,
            "planned": {"setup": len(spec["setup"]), "run": len(spec["run"]), "test": len(spec["test"]),
                        "serve": int(bool(serve)), "history": len(history) if tests else 0},
            "commands": [],
            "phases": dict.fromkeys(PHASES, "not_run"),
            "history": [],
            "serve": None,
            "overall": dict.fromkeys(("setup", "run", "test", "serve", "history"), "not_run"),
            "transcript": {"path": "run/transcript.jsonl", "lines": 0, "dropped": 0, "sha256": None},
            "error": None, "stopped": None, "notes": [],
        }
        if history and not tests:
            self.receipt["notes"].append("History commits were supplied but the spec has no test commands.")
        self.transcript = _Transcript(run_dir / "transcript.jsonl", self.start)

    # -- runtime helpers --------------------------------------------------
    def _runtime_log(self, label, stderr):
        text = mask_secrets(stderr.decode("utf-8", "replace"))[-MAX_LOG:]
        path = self.run_dir / "logs" / "runtime.log"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"# {label}\n{text}\n")

    def _exec_argv(self, cmd, workdir):
        return [self.binary, "exec", "-w", workdir, self.name, "sh", "-c", cmd]

    def _image(self):
        image = self.spec["image"]
        code, out, _ = _call([self.binary, "image", "inspect", image], timeout=60)
        if code != 0:
            self.receipt["image_pulled"] = True
            code, _, err = _call([self.binary, "pull", image], timeout=PULL_TIMEOUT)
            if code != 0:
                self._runtime_log("pull", err)
                return False
            code, out, _ = _call([self.binary, "image", "inspect", image], timeout=60)
        try:
            info = json.loads(out)[0]
        except (ValueError, IndexError, KeyError, TypeError):
            return code == 0
        if isinstance(info, dict):
            digests = [d for d in info.get("RepoDigests") or [] if isinstance(d, str) and DIGEST.fullmatch(d)]
            self.receipt["image_digest"] = digests[0] if digests else None
            image_id = info.get("Id")
            self.receipt["image_id"] = image_id if isinstance(image_id, str) and IMAGE_ID.fullmatch(image_id) else None
            os_name, arch = info.get("Os"), info.get("Architecture")
            if isinstance(os_name, str) and isinstance(arch, str) and re.fullmatch(r"[a-z0-9]{1,16}", os_name) \
                    and re.fullmatch(r"[a-z0-9_]{1,16}", arch):
                self.receipt["image_platform"] = f"{os_name}/{arch}"
        return True

    def _cpus(self):
        fmt = "{{.NCPU}}" if self.receipt["runtime"]["name"] == "docker" else "{{.Host.CPUs}}"
        code, out, _ = _call([self.binary, "info", "--format", fmt], timeout=INFO_TIMEOUT)
        try:
            available = int(out.strip())
        except ValueError:
            return CPUS
        return max(1, min(CPUS, available)) if code == 0 else CPUS

    def _create(self):
        cpus = self._cpus()
        self.receipt["limits"]["cpus"] = cpus
        argv = [self.binary, "create", "--name", self.name, "--label", LABEL, "--init",
                "--memory", MEMORY, "--cpus", str(cpus), "--pids-limit", str(PIDS),
                "--security-opt", "no-new-privileges", "--cap-drop", "ALL"]
        for cap in CAPS:
            argv += ["--cap-add", cap]
        argv += ["-w", WORKDIR]
        for key, value in ENV.items():
            argv += ["-e", f"{key}={value}"]
        if self.spec["network"] == "none":
            argv += ["--network", "none"]
        if self.serving:
            argv += ["-p", f"127.0.0.1::{self.spec['serve']['port']}"]
        argv += ["--entrypoint", "sleep", self.spec["image"], KEEPALIVE]
        self.attempted = True
        code, _, err = _call(argv)
        if code != 0:
            self._runtime_log("create", err)
        return code == 0

    def _place_copy(self):
        code, _, err = _call([self.binary, "cp", f"{self.copy_dir}/.", f"{self.name}:{WORKDIR}"],
                             timeout=COPY_TIMEOUT)
        if code != 0:
            self._runtime_log("copy", err)
        return code == 0

    def _start(self):
        code, _, err = _call([self.binary, "start", self.name])
        if code != 0:
            self._runtime_log("start", err)
            return False
        code, out, _ = _call([self.binary, "exec", self.name, "sh", "-c",
                              'for p in /proc/[0-9]*; do echo "${p#/proc/}"; done; echo "self $$"'])
        lines = out.decode("ascii", "replace").split()
        if code == 0 and "self" in lines:
            own = lines[lines.index("self") + 1:]
            pids = {int(x) for x in lines if x.isdigit()} - {int(x) for x in own if x.isdigit()}
            if 1 in pids:
                self.baseline = pids
        if self.baseline is None:
            self.receipt["notes"].append("Container processes could not be listed; stray processes were not cleaned between phases.")
        return True

    def _kill_stray(self):
        """Kill every container process started after the container itself (keeps init and sleep)."""
        if not self.baseline:
            return
        keep = " ".join(str(pid) for pid in sorted(self.baseline))
        script = (f'keep=" {keep} $$ "; for p in /proc/[0-9]*; do pid=${{p#/proc/}}; '
                  f'case "$keep" in *" $pid "*) ;; *) kill -9 "$pid" 2>/dev/null;; esac; done; true')
        _call([self.binary, "exec", self.name, "sh", "-c", script], timeout=30)

    def _network_off(self):
        if self.spec["network"] == "setup":
            code, out, _ = _call([self.binary, "inspect", "--format",
                                  "{{json .NetworkSettings.Networks}}", self.name])
            try:
                names = list(json.loads(out) or {}) if code == 0 else []
            except ValueError:
                names = []
            for net in names:
                if isinstance(net, str) and NETWORK_NAME.fullmatch(net):
                    _call([self.binary, "network", "disconnect", net, self.name])
        code, out, _ = _call([self.binary, "exec", self.name, "sh", "-c", "ls /sys/class/net"])
        ok = code == 0 and out.split() == [b"lo"]
        self.receipt["network_check"] = "verified-off" if ok else "failed"
        return ok

    def _remove(self):
        code, _, _ = _call([self.binary, "rm", "-f", self.name], timeout=CONTROL_TIMEOUT)
        if code != 0:
            code, out, _ = _call([self.binary, "ps", "-a", "-q", "--filter", f"name=^{self.name}$"])
            return code == 0 and not out.strip()
        return True

    # -- commands ---------------------------------------------------------
    def _save_log(self, rel, header, ex, code, timed_out, seconds):
        body = ex.text()
        footer = f"# exit code: {code if code is not None else 'none'}  seconds: {seconds:.3f}  timed out: {'yes' if timed_out else 'no'}\n"
        if ex.omitted:
            footer += f"# output truncated: {ex.omitted} bytes not saved\n"
        path = self.run_dir / rel.removeprefix("run/")
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(header + body + footer)
        return _sha256_file(path), body

    def _command(self, phase, n, command, workdir, order=None):
        rel = f"run/logs/{phase}-{order}-{n}.log" if order else f"run/logs/{phase}-{n}.log"
        ex = _Exec(self._exec_argv(command["cmd"], workdir), self.transcript, phase, n, order)
        code, timed_out = ex.wait(self.spec["timeout_seconds"])
        if timed_out:
            self._kill_stray()
        seconds = round(ex.t_end - ex.t_start, 3)
        cmd = mask_secrets(command["cmd"])
        header = (f"# frictional run log\n# phase: {phase}  command: {n}"
                  + (f"  history order: {order}" if order else "")
                  + f"  source: {command['source']}\n$ {cmd}\n")
        sha, body = self._save_log(rel, header, ex, code, timed_out, seconds)
        entry = {"phase": phase, "index": n, "source": command["source"], "cmd": cmd,
                 "exit_code": code, "timed_out": timed_out, "seconds": seconds,
                 "t_start": ex.t_start, "t_end": ex.t_end, "log": rel, "log_sha256": sha,
                 "output_truncated": bool(ex.omitted)}
        if order:
            entry["order"] = order
        if code != 0:
            entry["failure_category"] = failure_category(body, timed_out)
        return entry

    def _phase(self, phase, workdir=WORKDIR, order=None):
        rows = []
        for n, command in enumerate(self.spec["test" if phase == "history" else phase], 1):
            row = self._command(phase, n, command, workdir, order)
            rows.append(row)
            if phase == "setup" and row["exit_code"] != 0:
                break
        return rows

    def _outcome(self, phase, rows):
        if not rows:
            return "not_run"
        planned = len(self.spec[phase])
        return "passed" if len(rows) == planned and all(r["exit_code"] == 0 for r in rows) else "failed"

    def _run_history(self):
        for order, item in enumerate(self.history, 1):
            record = {"order": order, "commit": item["commit"], "outcome": "not_run", "logs": []}
            self.receipt["history"].append(record)
            target = f"{HISTORY_ROOT}/{order}"
            code, _, _ = _call([self.binary, "exec", self.name, "mkdir", "-p", target])
            if code == 0:
                try:
                    with open(item["tar"], "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as archive:
                        code, _, err = _call([self.binary, "cp", "-", f"{self.name}:{target}"],
                                             timeout=COPY_TIMEOUT, stdin=archive)
                except OSError:
                    code, err = None, b""
                if code != 0:
                    self._runtime_log(f"history {order} copy", err)
            if code != 0:
                record["note"] = "The archive could not be placed in the container."
                continue
            _call([self.binary, "exec", self.name, "sh", "-c",
                   f"if [ -d {WORKDIR}/node_modules ] && [ ! -e {target}/node_modules ]; then "
                   f"ln -s {WORKDIR}/node_modules {target}/node_modules; fi; true"])
            rows = self._phase("history", target, order)
            record["logs"] = [r["log"] for r in rows]
            self.receipt["commands"].extend(rows)
            failed = [r for r in rows if r["exit_code"] != 0]
            record["outcome"] = "failed" if failed else "passed"
            if failed:
                record["failure_category"] = failed[0]["failure_category"]
            self._kill_stray()

    def _serve(self):
        serve = self.spec["serve"]
        record = {"attempted": True, "source": serve["source"], "port": serve["port"],
                  "path": serve["path"], "host_port": None, "answered": False, "http_status": None,
                  "seconds_to_answer": None, "held_seconds": 0, "hold_interrupted": False,
                  "state": None, "exit_code": None, "log": None, "log_sha256": None}
        self.receipt["serve"] = record
        code, out, _ = _call([self.binary, "port", self.name, f"{serve['port']}/tcp"])
        match = re.search(rb"127\.0\.0\.1:(\d{1,5})\b", out) if code == 0 else None
        if not match:
            record["state"] = "port-unpublished"
            return
        host_port = int(match[1])
        record["host_port"] = host_port
        url = f"http://127.0.0.1:{host_port}{serve['path']}"
        ex = _Exec(self._exec_argv(serve["cmd"], WORKDIR), self.transcript, "serve", 1)
        began = time.monotonic()
        status = None
        try:
            while time.monotonic() - began < SERVE_WAIT and ex.running():
                status = _probe(url)
                if status is not None and status < 500:
                    record.update(answered=True, http_status=status,
                                  seconds_to_answer=round(time.monotonic() - began, 3))
                    break
                time.sleep(0.5)
            if record["answered"] and self.serve_seconds:
                print(f"frictional: serving {url} for {self.serve_seconds} s (Ctrl-C ends early)", flush=True)
                held = time.monotonic()
                try:
                    while time.monotonic() - held < self.serve_seconds and ex.running():
                        time.sleep(0.5)
                except KeyboardInterrupt:
                    record["hold_interrupted"] = True
                record["held_seconds"] = round(time.monotonic() - held, 1)
            if not record["answered"]:
                record["http_status"] = status
        finally:
            exited = not ex.running()
            record["state"] = "exited" if exited else "running"
            record["exit_code"] = ex.proc.returncode if exited else None
            ex.stop()
            self._kill_stray()
            seconds = round(ex.t_end - ex.t_start, 3)
            header = (f"# frictional run log\n# phase: serve  command: 1  source: {serve['source']}\n"
                      f"$ {mask_secrets(serve['cmd'])}\n")
            sha, _ = self._save_log("run/logs/serve-1.log", header, ex, record["exit_code"], False, seconds)
            record.update(log="run/logs/serve-1.log", log_sha256=sha)

    def _phases(self):
        r = self.receipt
        if not self._image():
            r["error"] = "image-unavailable"
            return
        if not self._create():
            r["error"] = "create-failed"
            return
        if not self._place_copy():
            r["error"] = "copy-failed"
            return
        if not self._start():
            r["error"] = "start-failed"
            return
        rows = self._phase("setup")
        r["commands"].extend(rows)
        r["phases"]["setup"] = self._outcome("setup", rows)
        if r["phases"]["setup"] == "failed":
            r["stopped"] = "setup-failed"
            return
        self._kill_stray()
        if self.spec["network"] != "all" and not self._network_off():
            r["stopped"] = "network-not-off"
            return
        for phase in ("run", "test"):
            rows = self._phase(phase)
            r["commands"].extend(rows)
            r["phases"][phase] = self._outcome(phase, rows)
            if rows:
                self._kill_stray()
        if r["planned"]["history"]:
            self._run_history()
        if self.serving:
            self._serve()

    def _finalize(self):
        r = self.receipt
        r["overall"].update(r["phases"])
        if r["serve"] is not None:
            r["overall"]["serve"] = "passed" if r["serve"]["answered"] else "failed"
        tested = [h["outcome"] for h in r["history"]]
        if any(o != "not_run" for o in tested):
            r["overall"]["history"] = ("passed" if len(tested) == r["planned"]["history"]
                                       and all(o == "passed" for o in tested) else "failed")
        r["finished_at"] = _now_iso()
        self.transcript.close()
        path = self.run_dir / "transcript.jsonl"
        r["transcript"].update(lines=self.transcript.count, dropped=self.transcript.dropped,
                               sha256=_sha256_file(path))
        with open(self.run_dir / "receipt.json", "x", encoding="utf-8") as handle:
            handle.write(json.dumps(r, indent=2) + "\n")

    def run(self):
        try:
            self._phases()
        except KeyboardInterrupt:
            self.receipt["stopped"] = "interrupted"
            raise
        finally:
            try:
                if self.attempted:
                    self.receipt["container"]["removed"] = self._remove()
            finally:
                self._finalize()
        return self.receipt


def _check_history(history):
    if history is None:
        return []
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        raise ValueError(f"history must be a list of at most {MAX_HISTORY} commits.")
    out = []
    for item in history:
        if not isinstance(item, dict) or set(item) != {"commit", "tar"}:
            raise ValueError("Each history item must have exactly 'commit' and 'tar'.")
        commit = item["commit"]
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise ValueError("A history commit must be a full lowercase commit SHA.")
        tar = Path(item["tar"])
        try:
            info = tar.lstat()
        except OSError as exc:
            raise ValueError("A history archive could not be found.") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ValueError("A history archive must be a regular file (not a symlink) of at most 512 MiB.")
        out.append({"commit": commit, "tar": tar})
    return out


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def run_project(root: Path, spec: dict, out_dir: Path, *, runtime: dict,
                history: list[dict] | None = None, serve: bool = False,
                serve_seconds: int = 0, revision: str | None = None) -> dict:
    """Install and run `root` in a fresh container per `spec`; write out_dir/run/ and return the receipt.

    `history` items are {"commit": FULL_SHA, "tar": Path} archives of the project
    folder (no prefix), oldest first; each is unpacked under /history/<order> in
    the same container after setup and the spec's test commands run there.
    """
    spec = check_spec(spec)
    if not isinstance(runtime, dict) or runtime.get("name") not in ("docker", "podman"):
        raise ValueError("runtime must be the dictionary returned by find_runtime().")
    binary = shutil.which(runtime["name"])
    if not binary:
        raise ValueError(f"The {runtime['name']} command is not available.")
    version = runtime.get("version")
    runtime = {"name": runtime["name"],
               "version": version if isinstance(version, str) and VERSION.fullmatch(version) else "unknown"}
    if revision is not None and (not isinstance(revision, str) or not SHA.fullmatch(revision)):
        raise ValueError("revision must be a full lowercase commit SHA.")
    if type(serve) is not bool or type(serve_seconds) is not int or not 0 <= serve_seconds <= MAX_HOLD:
        raise ValueError("serve must be true or false and serve_seconds an integer 0 to 3600.")
    if serve and spec["serve"] is None:
        raise ValueError("serve was requested but the run spec has no 'serve' command.")
    if serve_seconds and not serve:
        raise ValueError("serve_seconds needs serve=True.")
    history = _check_history(history)
    root, out_dir = Path(root), Path(out_dir)
    try:
        with pe.directory(root):
            pass
    except OSError as exc:
        raise ValueError("Project root is not a readable ordinary directory.") from exc
    if not out_dir.is_dir():
        raise ValueError("Output directory must already exist.")
    if _within(out_dir.resolve(), root.resolve()):
        raise ValueError("Output directory must be outside the project.")
    run_dir = out_dir / "run"
    if os.path.lexists(run_dir):
        raise ValueError("run/ already exists in the output directory; use a new output directory.")
    temp = Path(tempfile.mkdtemp(prefix="frictional-copy-"))
    try:
        work = temp / "work"
        work.mkdir(mode=0o755)
        copy_stats = _copy_tree(root, work)
        try:
            os.mkdir(run_dir)
        except FileExistsError as exc:
            raise ValueError("run/ already exists in the output directory; use a new output directory.") from exc
        (run_dir / "logs").mkdir()
        runner = _Runner(binary, runtime, spec, run_dir, work, copy_stats, history,
                         serve, serve_seconds, revision)
        return runner.run()
    finally:
        shutil.rmtree(temp, ignore_errors=True)


# --------------------------------------------------------------------------
# Receipt -> Frictional dimension results


def _shape(condition):
    if not condition:
        raise ValueError("receipt shape")


def _rows(receipt, phase):
    rows = [c for c in receipt["commands"] if c["phase"] == phase]
    for c in rows:
        _shape(isinstance(c, dict) and SOURCE.fullmatch(c["source"]) and LOG_PATH.fullmatch(c["log"]))
        _shape(type(c["index"]) is int and type(c["timed_out"]) is bool)
        _shape(c["exit_code"] is None or type(c["exit_code"]) is int)
        _shape(type(c["seconds"]) in (int, float) and c["seconds"] >= 0)
        if c["exit_code"] != 0:
            _shape(c.get("failure_category") in {"syntax", "dependency", "build", "assertion",
                                                 "timeout", "other"})
    return rows


def _sources(rows):
    return "; ".join(dict.fromkeys(r["source"] for r in rows)) or "none"


def _categories(rows):
    counts = collections.Counter(r["failure_category"] for r in rows if r["exit_code"] != 0)
    return ", ".join(f"{k}: {counts[k]}" for k in sorted(counts)) or "none"


def _defaulted(rows):
    return any(r["source"].startswith("default:") for r in rows)


def _command_fact(c, limit, label):
    if c["timed_out"]:
        text = (f"{label} command {c['index']} (source {c['source']}) was stopped at the "
                f"{limit} s limit; failure category timeout.")
    else:
        text = (f"{label} command {c['index']} (source {c['source']}) exited {c['exit_code']} "
                f"after {c['seconds']:.1f} s; failure category {c['failure_category']}.")
    return pe.fact(text, c["log"], ALT_CLEAN)


def _results(receipt):
    _shape(isinstance(receipt, dict) and receipt.get("runner") == "frictional")
    planned = receipt["planned"]
    _shape(isinstance(planned, dict) and all(type(planned[k]) is int and planned[k] >= 0
                                             for k in ("setup", "run", "test", "serve", "history")))
    _shape(isinstance(receipt["commands"], list) and isinstance(receipt["history"], list))
    runtime = receipt["runtime"]
    _shape(runtime["name"] in ("docker", "podman") and VERSION.fullmatch(runtime["version"]))
    image = receipt["image"]
    _shape(isinstance(image, str) and IMAGE.fullmatch(image))
    revision = receipt["revision"]
    _shape(revision is None or SHA.fullmatch(revision))
    phases = receipt["phases"]
    _shape(all(phases[p] in OUTCOMES for p in PHASES))
    limit = receipt["limits"]["timeout_seconds"]
    _shape(type(limit) is int)
    setup_net, run_net = receipt["network_during_setup"], receipt["network_during_run"]
    _shape(setup_net in ("on", "off") and run_net in ("on", "off"))
    error, stopped = receipt["error"], receipt["stopped"]
    _shape(error is None or error in ERRORS)
    _shape(stopped is None or stopped in STOPS)
    skipped = receipt["copy"]["credential_like_skipped"]
    _shape(type(skipped) is int)
    out = {}
    counts = {"install-test": planned["setup"], "run-check": planned["run"] + planned["test"],
              "test-history": planned["history"], "serve-check": planned["serve"]}
    if error:
        for dim in RUN_DIMENSIONS:
            out[dim] = (pe.blocked(LOCATOR, f"The container run did not start: {ERRORS[error]}.")
                        if counts[dim] else pe.absent())
        return out
    context = (f"fresh {runtime['name']} {runtime['version']} container, image {image}, "
               f"digest {'recorded' if receipt['image_digest'] else 'not recorded'}, "
               + (f"revision {revision[:12]}" if revision else "working-tree copy with no revision recorded"))
    reason = STOPS.get(stopped, "an earlier phase stopped the run")

    # install-test
    rows = _rows(receipt, "setup")
    if not planned["setup"]:
        out["install-test"] = pe.absent()
    elif not rows:
        out["install-test"] = pe.blocked(LOCATOR, f"Setup commands were not run because {reason}.")
    else:
        ok = sum(r["exit_code"] == 0 for r in rows)
        alts = [ALT_CLEAN, ALT_INSTALL]
        if _defaulted(rows):
            alts.append(ALT_DEFAULT)
        if setup_net == "on":
            alts.append(ALT_NETWORK)
        if skipped:
            alts.append(f"{skipped} credential-like file(s) were not copied into the container; "
                        "a project that depends on them can fail for that reason.")
        facts = [pe.fact(f"Setup in a {context}; network {setup_net}: {ok}/{planned['setup']} command(s) "
                         f"exited 0 in {sum(r['seconds'] for r in rows):.1f} s; outcome {phases['setup']}. "
                         f"Sources: {_sources(rows)}.", LOCATOR, *alts)]
        facts += [_command_fact(r, limit, "Setup") for r in rows if r["exit_code"] != 0]
        if phases["setup"] == "failed":
            facts.append(pe.fact("Run, test, history and serve phases were not run because setup failed.",
                                 LOCATOR))
        out["install-test"] = pe.reported(*facts)

    # run-check
    if not planned["run"] and not planned["test"]:
        out["run-check"] = pe.absent()
    else:
        facts, ran = [], False
        for phase, label in (("run", "Run"), ("test", "Test")):
            if not planned[phase]:
                continue
            rows = _rows(receipt, phase)
            if not rows:
                facts.append(pe.fact(f"{label} commands were not run because {reason}.", LOCATOR))
                continue
            ran = True
            ok = sum(r["exit_code"] == 0 for r in rows)
            alts = [ALT_EXIT, ALT_CLEAN] if phase == "run" else [ALT_TESTS, ALT_CLEAN]
            if _defaulted(rows):
                alts.append(ALT_DEFAULT)
            if run_net == "on":
                alts.append(ALT_NETWORK)
            categories = f" Failure categories: {_categories(rows)}." if phase == "test" else ""
            facts.append(pe.fact(
                f"{label} phase in a {context}; network {run_net}: {len(rows)}/{planned[phase]} command(s) ran, "
                f"{ok} exited 0, in {sum(r['seconds'] for r in rows):.1f} s; outcome {phases[phase]}."
                f"{categories} Sources: {_sources(rows)}.", LOCATOR, *alts))
            facts += [_command_fact(r, limit, label) for r in rows if r["exit_code"] != 0]
        out["run-check"] = (pe.reported(*facts) if ran else
                            pe.blocked(LOCATOR, f"Run and test commands were not run because {reason}."))

    # test-history
    history = receipt["history"]
    if not planned["history"]:
        out["test-history"] = pe.absent()
    elif not history:
        out["test-history"] = pe.blocked(LOCATOR, f"Supplied commits were not tested because {reason}.")
    else:
        for h in history:
            _shape(isinstance(h, dict) and h["outcome"] in OUTCOMES and SHA.fullmatch(h["commit"]))
            if h["outcome"] == "failed":
                _shape(h.get("failure_category") in {"syntax", "dependency", "build", "assertion",
                                                     "timeout", "other"})
        outcomes = [h["outcome"] for h in history] + ["not_run"] * (planned["history"] - len(history))
        count = collections.Counter(outcomes)
        tested = [o for o in outcomes if o != "not_run"]
        fail_pass = sum(a == "failed" and b == "passed" for a, b in zip(tested, tested[1:]))
        pass_fail = sum(a == "passed" and b == "failed" for a, b in zip(tested, tested[1:]))
        runs, word = [], {"passed": "pass", "failed": "fail", "not_run": "not run"}
        for outcome in outcomes:
            if runs and runs[-1][0] == outcome:
                runs[-1][1] += 1
            else:
                runs.append([outcome, 1])
        sequence = ", ".join(f"{n} {word[o]}" for o, n in runs)
        cats = collections.Counter(h["failure_category"] for h in history if h["outcome"] == "failed")
        cat_text = ", ".join(f"{k}: {cats[k]}" for k in sorted(cats)) or "none"
        rows = _rows(receipt, "history")
        alts = [ALT_HISTORY, ALT_ORDER, ALT_TESTS]
        if _defaulted(rows):
            alts.append(ALT_DEFAULT)
        out["test-history"] = pe.reported(pe.fact(
            f"Test commands ran at {len(tested)}/{planned['history']} supplied commit(s), in the supplied order, "
            f"after setup at the current tree in a {context}: {count['passed']} passed, {count['failed']} failed, "
            f"{count['not_run']} not run. Outcome runs in order: {sequence}. Failure categories: {cat_text}. "
            f"Transitions between consecutive tested commits: fail to pass {fail_pass}, pass to fail {pass_fail}. "
            f"Sources: {_sources(rows)}.", LOCATOR, *alts))

    # serve-check
    serve = receipt["serve"]
    if not planned["serve"]:
        out["serve-check"] = pe.absent()
    elif not serve:
        out["serve-check"] = pe.blocked(LOCATOR, f"The serve command was not run because {reason}.")
    else:
        _shape(SOURCE.fullmatch(serve["source"]) and HTTP_PATH.fullmatch(serve["path"]))
        _shape(type(serve["port"]) is int and type(serve["answered"]) is bool)
        _shape(serve["log"] is None or LOG_PATH.fullmatch(serve["log"]))
        where = f"container port {serve['port']}, path {serve['path']}"
        if serve["answered"]:
            _shape(type(serve["http_status"]) is int and type(serve["seconds_to_answer"]) in (int, float))
            _shape(type(serve["held_seconds"]) in (int, float))
            out["serve-check"] = pe.reported(pe.fact(
                f"Serve command (source {serve['source']}) answered with HTTP status {serve['http_status']} at "
                f"{where}, {serve['seconds_to_answer']:.1f} s after it started, in a {context}; network on; "
                f"kept up {serve['held_seconds']:.0f} s more.", serve["log"] or LOCATOR, ALT_SERVE, ALT_CLEAN))
        else:
            state = serve["state"]
            if state == "exited":
                _shape(serve["exit_code"] is None or type(serve["exit_code"]) is int)
                detail = f"the process exited with code {serve['exit_code']} first"
            elif state == "running":
                detail = "the process was still running when it was stopped"
            elif state == "port-unpublished":
                detail = "the port could not be published"
            else:
                raise ValueError("receipt shape")
            out["serve-check"] = pe.reported(pe.fact(
                f"Serve command (source {serve['source']}) gave no HTTP answer below status 500 at {where} "
                f"within {SERVE_WAIT} s in a {context}; {detail}.", serve["log"] or LOCATOR,
                ALT_SERVE_FAIL, ALT_CLEAN))
    return out


def run_results(receipt: dict) -> dict[str, dict]:
    """Map a run receipt to install-test, run-check, test-history and serve-check results."""
    try:
        return _results(receipt)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return {dim: pe.blocked(LOCATOR, "Run receipt does not match the expected shape.")
                for dim in RUN_DIMENSIONS}
