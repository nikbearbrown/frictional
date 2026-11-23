"""Read-only Git CLI calls for a project folder that sits inside a larger repository.

A course repository keeps each student's work in a subfolder, so the folder has no
.git of its own. These helpers read the enclosing repository's first-parent history
for that folder and archive the folder at a commit (for the optional container run).

Repository-local Git config is untrusted: several settings can make Git start a
program. Every call overrides those settings, ignores global and system config,
never prompts, and never runs hooks. Only `log` and `archive` are used.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

TIMEOUT = 30
MAX_COMMITS = 250
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SAFE_CONFIG = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=false",
    "-c", "log.showSignature=false",
    "-c", "gpg.program=false",
    "-c", "protocol.allow=never",
    "-c", "diff.external=",
    "-c", "core.untrackedCache=false",
)


def _env() -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def _git(repo: Path, *args: str, binary: bool = False):
    git = shutil.which("git")
    if git is None:
        raise OSError("git not found")
    done = subprocess.run([git, *SAFE_CONFIG, "--no-pager", "-C", str(repo), *args],
                          capture_output=True, env=_env(), timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise OSError("git command failed")
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def find_enclosing(root: Path) -> tuple[Path, str] | None:
    """Return (repository root, folder path inside it) when root has no .git of its own."""
    root = Path(root).resolve()
    if (root / ".git").exists():
        return None
    for depth, parent in enumerate(root.parents):
        if depth >= 12:
            break
        if (parent / ".git").exists():
            return parent, root.relative_to(parent).as_posix()
    return None


def folder_history(repo: Path, subpath: str, limit: int = MAX_COMMITS):
    """First-parent commits of HEAD that touch subpath, newest first.

    Returns (rows, state) with rows shaped like process_evidence.history():
    (sha, author_time, committer_time, author_identity_digest, is_merge).
    The identity digest stays in memory and is only counted, never reported.
    """
    try:
        text = _git(repo, "log", "--first-parent", f"--max-count={limit + 1}",
                    "--format=%H%x1f%at%x1f%ct%x1f%an <%ae>%x1f%P", "HEAD", "--",
                    f":(literal){subpath}" if subpath else ".")
    except (OSError, subprocess.TimeoutExpired):
        return [], "unavailable"
    rows = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5 or not SHA.fullmatch(parts[0]):
            return [], "unavailable"
        try:
            author = dt.datetime.fromtimestamp(int(parts[1]), dt.timezone.utc)
            committer = dt.datetime.fromtimestamp(int(parts[2]), dt.timezone.utc)
        except (ValueError, OverflowError, OSError):
            return [], "unavailable"
        party = hashlib.sha256(parts[3].encode("utf-8", "replace")).digest()
        rows.append((parts[0], author, committer, party, len(parts[4].split()) > 1))
    if not rows:
        return [], "absent"
    if len(rows) > limit:
        return rows[:limit], "partial"
    return rows, "complete"


def archive_folder(repo: Path, commit: str, subpath: str, dest: Path) -> Path:
    """Write a tar of the folder's contents at commit (folder contents at the tar root)."""
    if not SHA.fullmatch(commit):
        raise ValueError("commit must be a full lowercase SHA")
    tree = f"{commit}:{subpath}" if subpath else commit
    data = _git(repo, "archive", "--format=tar", tree, binary=True)
    dest = Path(dest)
    with open(dest, "xb") as f:
        f.write(data)
    return dest
