"""Bounded, local process observations. Never run project code or Git.

collect(root, supplied=None, starter=None, deadline=None) returns one result per
DIMENSIONS. A supplied key uses its dimension ID; omitted means no evidence.
Supported packet (individual fields are strict; unknown/malformed keys fail closed):

* push-timeline: {"events": [{"at": ISO_TIME, "kind": "push"|"ci"}]}
* install-test: {"attested": true, "isolated": true,
  "outcome": "passed"|"failed"|"inconclusive", "elapsed_seconds": 0..86400,
  optional "revision": LOWERCASE_FULL_COMMIT_SHA,
  optional "environment": "posix-container"|"isolated-vm"|"sandbox",
  optional "receipt_sha256": LOWERCASE_SHA256}. No raw transcript is accepted.
* error-trajectory: {"events": [{"at": ISO_TIME, "outcome": "pass"|"fail",
  "category": "assertion"|"build"|"config"|"dependency"|"environment"|"syntax"|"other"}]}
  "category" is required for failures and omitted for passes.
* temporal-engagement: {"milestones": [{"start": ISO_TIME, "end": ISO_TIME,
  "difficulty": "low"|"medium"|"high"}], optional "grain": "item"} (nonoverlapping,
  counts only; "item" marks timed probe items rather than assignment milestones)
* transfer, calibration, retrieval, scaffolding: {"observations": [...]}
  transfer rows: {"distance": "near"|"far", "correct": BOOL}
  calibration rows: {"confidence": 0..1, "correct": BOOL}
  retrieval rows: {"delay_hours": 0..87600, "correct": BOOL}
  scaffolding rows: {"level": "none"|"hint"|"worked", "successful": BOOL}; the
  scaffolding packet may add "design": "ladder" when each student moved up a hint
  ladder (none, then partial, then full), which reports conditional success per
  rung and never the preprint's ratio

ISO_TIME needs an explicit timezone. Lists are capped at 500 and each adapter
uses fixed output phrases, not supplied raw text, identities, URLs or paths.
Supplied observations are unverified, pre-normalized evidence, not access to
an external system. Never use student/identifying records in this prototype.
Static filesystem inspection requires POSIX no-follow, directory-relative reads.
Git history covers verified HEAD first-parent objects, not branches, Git index,
remote evidence, linked worktrees or alternates. Supports loose SHA-1/SHA-256
and bounded local SHA-1 v2 pack indexes (16 MiB total, eight packs maximum).
"""

from __future__ import annotations

import bisect
import collections
import contextlib
import datetime as dt
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import zlib

DIMENSIONS = (
    "commit-history", "push-timeline", "install-test", "readme-completeness",
    "naming-conventions", "repo-hygiene", "template-delta",
    "contribution-distribution", "process-log", "error-trajectory",
    "temporal-engagement", "transfer", "calibration", "retrieval", "scaffolding",
    # course process files (course_evidence.COURSE_DIMENSIONS)
    "log-rubric-rows", "log-commit-consistency", "prediction-log",
    "verification-notes", "contributions-log",
)
MAX_ENTRIES = 3000
MAX_OBSERVATIONS = 500
MAX_COMMITS = 250
MAX_FILE = 131072
MAX_OBJECT = 262144
MAX_COMPARE_BYTES = 8 * 1024 * 1024
MAX_PACK_BYTES = 16 * 1024 * 1024
MAX_PACK_FILES = 8
HEX = re.compile(rb"[0-9a-f]{40}|[0-9a-f]{64}")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".tox", ".cache", "dist", "build", "coverage", ".next", "target", "site-packages"}
CREDENTIAL = re.compile(
    r"(^|[._-])(?:env|credentials?|secrets?|tokens?|keyring|private[-_]?key|"
    r"id[-_](?:rsa|dsa|ecdsa|ed25519)|pem|pfx|p12|key)(?:[._-]|$)", re.I)
UTC = dt.timezone.utc


def fact(observation, locator, *alternatives):
    return {"observation": observation, "locator": locator,
            "alternative_explanations": list(alternatives)}


def result(status, *facts):
    return {"status": status, "facts": list(facts)}


def reported(*facts):
    return result("reported", *facts)


def absent():
    return result("no_evidence")


def blocked(locator, message):
    return result("blocked", fact(message, locator))


def valid_relative(path):
    p = PurePosixPath(path)
    return not p.is_absolute() and bool(p.parts) and ".." not in p.parts and "\\" not in path


@contextlib.contextmanager
def directory(path):
    if not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")):
        raise OSError("POSIX safe-open flags unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def path_dir(root, parts):
    with directory(root) as first:
        with contextlib.ExitStack() as stack:
            fd = first
            for part in parts:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                stack.callback(os.close, nxt)
                fd = nxt
            yield fd


def read_bounded(root, relative, cap=MAX_FILE):
    if not valid_relative(relative):
        raise ValueError("invalid path")
    parts = PurePosixPath(relative).parts
    with path_dir(root, parts[:-1]) as parent:
        detected = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(detected.st_mode) or detected.st_nlink != 1 or detected.st_size > cap:
            raise ValueError("not a bounded single-link regular file")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            initial = os.fstat(fd)
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or initial.st_size > cap:
                raise ValueError("not a bounded single-link regular file")
            with os.fdopen(os.dup(fd), "rb") as f:
                data = f.read(cap + 1)
            final = os.fstat(fd)
            if (len(data) != initial.st_size or len(data) > cap
                    or (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns)):
                raise ValueError("changed file or limit")
            return data
        finally:
            os.close(fd)


def inventory(root):
    files, total, symlinks, omitted, credential_count, build_count, full = {}, 0, 0, 0, 0, 0, True
    todo = [()]
    while todo and full:
        parts = todo.pop()
        try:
            with path_dir(root, parts) as fd:
                # A bounded prefix suffices; never materialize an unbounded directory.
                with os.scandir(fd) as it:
                    batch = []
                    for item in it:
                        if total >= MAX_ENTRIES:
                            full = False
                            break
                        total += 1
                        try:
                            batch.append((item.name, item.stat(follow_symlinks=False)))
                        except OSError:
                            omitted += 1
                for name, st in sorted(batch):
                    path = (*parts, name)
                    if stat.S_ISLNK(st.st_mode):
                        symlinks += 1
                    elif name in SKIP and stat.S_ISDIR(st.st_mode):
                        if name not in {".git"}:
                            build_count += 1
                    elif CREDENTIAL.search(name):
                        credential_count += 1
                    elif stat.S_ISDIR(st.st_mode):
                        if name.startswith("."):
                            # Hidden folders (.github, .vscode, .streamlit) are skipped by policy.
                            # They cannot hide a top-level README or FRICTIONAL.md, so they do not
                            # make the inventory partial.
                            continue
                        if len(path) >= 12:
                            omitted += 1
                        else:
                            todo.append(path)
                    elif stat.S_ISREG(st.st_mode):
                        if st.st_nlink != 1:
                            omitted += 1
                        else:
                            files["/".join(path)] = st.st_size
                    else:
                        omitted += 1
        except (OSError, ValueError):
            omitted += 1
    return files, (total, symlinks, omitted, credential_count, build_count, full)


def timestamp(s):
    if not isinstance(s, str) or not STAMP.fullmatch(s):
        raise ValueError("timestamp")
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def packet(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("schema")
    return value


def items(value, key):
    records = packet(value, (key,))[key]
    if not isinstance(records, list) or len(records) > MAX_OBSERVATIONS:
        raise ValueError("row limit")
    return records


def finite(v, low, high):
    if type(v) not in (int, float) or not low <= v <= high or not math.isfinite(v):
        raise ValueError("number")
    return v


def boolean(v):
    if type(v) is not bool:
        raise ValueError("boolean")
    return v


def choice(v, options):
    if not isinstance(v, str) or v not in options:
        raise ValueError("enumeration")
    return v


def git_head(root):
    head = read_bounded(root, ".git/HEAD", 512).strip()
    if HEX.fullmatch(head):
        return head
    match = re.fullmatch(rb"ref: (refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9_./-]{0,210})", head)
    if not match:
        raise ValueError("unresolved ref")
    raw = match[1]
    reference = raw.decode("ascii")
    if not valid_relative(reference) or b"//" in raw or b"/." in raw:
        raise ValueError("invalid ref")
    try:
        oid = read_bounded(root, ".git/" + reference, 512).strip()
    except FileNotFoundError:
        packed = read_bounded(root, ".git/packed-refs")
        ids = [line.split(b" ", 1)[0] for line in packed.splitlines()
               if line.endswith(b" " + raw)]
        if len(ids) != 1:
            raise ValueError("ambiguous ref") from None
        oid = ids[0]
    if not HEX.fullmatch(oid):
        raise ValueError("invalid oid")
    return oid


def parse_commit(raw, oid):
    prefix = b"commit " + str(len(raw)).encode() + b"\0"
    digest = (hashlib.sha1 if len(oid) == 40 else hashlib.sha256)(prefix + raw).hexdigest()
    if digest != oid.decode("ascii"):
        raise ValueError("unverified commit object")
    return commit_headers(raw, oid)


def loose_commit(root, oid):
    obj = oid.decode("ascii")
    compressed = read_bounded(root, f".git/objects/{obj[:2]}/{obj[2:]}", MAX_OBJECT)
    decomp = zlib.decompressobj()
    data = decomp.decompress(compressed, MAX_OBJECT + 1)
    if len(data) > MAX_OBJECT or not decomp.eof or decomp.unused_data:
        raise ValueError("invalid or oversized loose object")
    header, separator, raw = data.partition(b"\0")
    if not separator or header != b"commit " + str(len(raw)).encode():
        raise ValueError("invalid loose commit object")
    return parse_commit(raw, oid)


def commit_headers(raw, oid):
    headers = raw.partition(b"\n\n")[0].split(b"\n")
    times = {}
    parties = {}
    parents = []
    for line in headers:
        key, _, value = line.partition(b" ")
        if key == b"parent":
            if not HEX.fullmatch(value) or len(oid) != len(value):
                raise ValueError("invalid parent")
            parents.append(value)
        if key in {b"author", b"committer"}:
            m = re.fullmatch(rb"(.+) (-?\d{1,12}) [+-]\d{4}", value)
            if not m or key in times:
                raise ValueError("invalid identity or timestamp")
            times[key] = dt.datetime.fromtimestamp(int(m[2]), UTC)
            # Input bytes stay in memory; never return identity or its hash.
            parties[key] = hashlib.sha256(m[1]).digest()
    if set(times) != {b"author", b"committer"}:
        raise ValueError("incomplete headers")
    return parents, times[b"author"], times[b"committer"], parties[b"author"]



def u32(data, offset):
    return struct.unpack_from("!I", data, offset)[0]


def load_packs(root):
    """Read only small ordinary SHA-1 v2 index/pack pairs, ignoring ancillary paths."""
    candidates = {}
    try:
        with path_dir(root, (".git", "objects", "pack")) as parent:
            with os.scandir(parent) as entries:
                for i, ent in enumerate(entries):
                    if i >= 128:
                        break
                    m = re.fullmatch(r"(pack-[a-f0-9]{40})\.(idx|pack)", ent.name)
                    if not m:
                        continue
                    st = ent.stat(follow_symlinks=False)
                    if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                        candidates.setdefault(m[1], {})[m[2]] = st.st_size
    except OSError:
        return []
    packs = []
    remaining = MAX_PACK_BYTES
    for name, lengths in sorted(candidates.items()):
        if len(packs) >= MAX_PACK_FILES or set(lengths) != {"idx", "pack"}:
            continue
        total = sum(lengths.values())
        if total > remaining:
            continue
        remaining -= total
        try:
            idx = read_bounded(root, f".git/objects/pack/{name}.idx", lengths["idx"])
            pack = read_bounded(root, f".git/objects/pack/{name}.pack", lengths["pack"])
            n = u32(idx, 1028)
            start = 1032
            offset_table = start + n * 24
            large_table = offset_table + n * 4
            if (not idx.startswith(b"\xfftOc\0\0\0\x02") or len(idx) < 1072
                    or not pack.startswith(b"PACK") or u32(pack, 4) not in (2, 3)
                    or u32(pack, 8) != n or large_table > len(idx)-40
                    or len(pack) < 32
                    or hashlib.sha1(idx[:-20]).digest() != idx[-20:]
                    or idx[-40:-20] != pack[-20:]
                    or hashlib.sha1(pack[:-20]).digest() != pack[-20:]):
                continue
            packs.append((idx, pack, n, start, offset_table, large_table))
        except (OSError, ValueError, struct.error):
            continue
    return packs


def pack_lookup(packs, oid):
    raw = bytes.fromhex(oid.decode("ascii"))
    for info in packs:
        idx, pack, n, start, offsets, large = info
        i = bisect.bisect_left(range(n), raw, key=lambda j: idx[start+j*20:start+(j+1)*20])
        if i < n and idx[start+i*20:start+(i+1)*20] == raw:
            off = u32(idx, offsets+4*i)
            if off & 0x80000000:
                p = large + (off & 0x7fffffff)*8
                if p+8 > len(idx)-40:
                    raise ValueError("invalid 64-bit offset")
                off = struct.unpack_from("!Q", idx, p)[0]
            if not 12 <= off < len(pack)-20:
                raise ValueError("invalid object offset")
            return info, off
    raise ValueError("packed object not found")


def pack_byte(data, offset):
    if not 0 <= offset < len(data):
        raise ValueError("truncated pack")
    return data[offset]


def apply_delta(base, delta):
    def varint(pos):
        result = 0
        for shift in range(0, 35, 7):
            b = pack_byte(delta, pos)
            pos += 1
            result |= (b & 127) << shift
            if not b & 128:
                return result, pos
        raise ValueError("delta varint too long")

    size_base, pos = varint(0)
    size_out, pos = varint(pos)
    if size_base != len(base) or size_out > MAX_OBJECT:
        raise ValueError("delta size")
    out = bytearray()
    while pos < len(delta):
        op = delta[pos]
        pos += 1
        if op & 128:
            off = size = 0
            for bit in range(7):
                if not op & (1 << bit):
                    continue
                b = pack_byte(delta, pos)
                pos += 1
                if bit < 4:
                    off |= b << (8*bit)
                else:
                    size |= b << (8*(bit-4))
            size = size or 65536
            if off + size > len(base) or len(out) + size > size_out:
                raise ValueError("delta copy out of bounds")
            out.extend(base[off:off+size])
        elif op:
            if pos + op > len(delta) or len(out) + op > size_out:
                raise ValueError("delta insert out of bounds")
            out.extend(delta[pos:pos+op])
            pos += op
        else:
            raise ValueError("reserved delta opcode")
    if len(out) != size_out:
        raise ValueError("incomplete delta")
    return bytes(out)


def pack_object(packs, info, pos, depth=0):
    if depth >= 8:
        raise ValueError("delta depth limit")
    raw = info[1]
    view = memoryview(raw)[:-20]
    b = pack_byte(view, pos)
    size, kind, shift = b & 15, (b >> 4) & 7, 4
    initial = pos
    pos += 1
    while b & 128:
        if shift > 25:
            raise ValueError("pack size too large")
        b = pack_byte(view, pos)
        pos += 1
        size |= (b & 127) << shift
        shift += 7
    if size > MAX_OBJECT or kind not in (1, 6, 7):
        raise ValueError("only bounded commit objects considered")
    if kind == 6:
        b = pack_byte(view, pos)
        pos += 1
        offset = b & 127
        while b & 128:
            if offset > len(raw):
                raise ValueError("invalid delta offset")
            b = pack_byte(view, pos)
            pos += 1
            offset = ((offset + 1) << 7) | (b & 127)
        parent = initial - offset
        if not 12 <= parent < initial:
            raise ValueError("invalid delta base")
        packed_base = (info, parent)
    elif kind == 7:
        if pos + 20 >= len(view):
            raise ValueError("invalid delta base oid")
        packed_base = pack_lookup(packs, bytes(view[pos:pos+20]).hex().encode())
        pos += 20
    else:
        packed_base = None
    decomp = zlib.decompressobj()
    content = decomp.decompress(view[pos:min(len(view), pos + MAX_OBJECT*2)], size+1)
    if len(content) != size or not decomp.eof:
        raise ValueError("malformed or oversized compressed object")
    if packed_base is not None:
        content = apply_delta(pack_object(packs, *packed_base, depth=depth+1), content)
    return content


def packed_commit(packs, oid):
    return parse_commit(pack_object(packs, *pack_lookup(packs, oid)), oid)


def history(root):
    try:
        st = (root / ".git").lstat()
    except FileNotFoundError:
        return [], "absent"
    except OSError:
        return [], "unavailable"
    if not stat.S_ISDIR(st.st_mode):
        return [], "unavailable"
    try:
        oid = git_head(root)
    except (OSError, ValueError):
        return [], "unavailable"
    rows, seen, packs = [], set(), None
    while oid:
        if oid in seen or len(rows) >= MAX_COMMITS:
            return rows, "partial"
        try:
            try:
                record = loose_commit(root, oid)
            except FileNotFoundError:
                if packs is None:
                    packs = load_packs(root) if len(oid) == 40 else []
                record = packed_commit(packs, oid)
            parents, author, committer, party = record
        except (OSError, ValueError, OverflowError, IndexError, struct.error, zlib.error):
            return rows, "partial" if rows else "unavailable"
        seen.add(oid)
        rows.append((oid.decode(), author, committer, party, len(parents) > 1))
        oid = parents[0] if parents else b""
    return rows, "complete"


def commit_findings(rows, state, deadline):
    loc = "git:HEAD:first-parent"
    if not rows:
        return (absent() if state == "absent" else
                blocked(loc, "Local history unavailable within bounded local loose/v2 pack parsing; shared objects, linked worktrees or inaccessible history may be omitted."))
    a, c = sorted(r[1] for r in rows), sorted(r[2] for r in rows)
    span = lambda ts: (ts[-1] - ts[0]).total_seconds() / 3600
    sessions = 1 + sum((right-left).total_seconds() > 90*60 for left, right in zip(a, a[1:]))
    evidence = [fact(
        f"{len(rows)} verified HEAD first-parent commit object(s); coverage: {state}; "
        f"{sum(r[4] for r in rows)} with multiple parents. Side branches were not traversed.", loc,
        "Squash merges, rebases, alternate branches or work outside Git can change the visible record."),
        fact(f"Author-date span {span(a):.2f} h; committer-date span {span(c):.2f} h; "
             f"{sum(r[1]!=r[2] for r in rows)} author/committer timestamp difference(s); "
             f"{len({t.date() for t in a})} distinct UTC author calendar day(s); "
             f"{sessions} author-date cluster(s) separated by gaps greater than 90 minutes.", loc,
             "Timestamps are editable; author and committer dates are not observed work, elapsed effort or push times.",
             "Offline work, rebases and clock differences can alter these patterns.")]
    if deadline is not None:
        try:
            end = timestamp(deadline)
            start = end - dt.timedelta(hours=24)
        except (ValueError, OverflowError):
            evidence.append(fact("Deadline not compared because no timezone-qualified timestamp was supplied.", "input:deadline"))
        else:
            n = sum(start <= r[1] <= end for r in rows)
            evidence.append(fact(f"Author dates in the inclusive final 24-hour window: {n}/{len(rows)}.", loc,
                                 "Author dates do not establish when work occurred or reached a server."))
    return reported(*evidence)


def contributions(rows, state):
    if not rows:
        return absent() if state == "absent" else blocked("git:HEAD:first-parent", "Local history is unavailable.")
    counts = collections.Counter(r[3] for r in rows)
    return reported(fact(
        f"{len(counts)} distinct exact author metadata value(s); largest bucket {max(counts.values())}/{len(rows)} "
        f"first-parent commit object(s); coverage: {state}. Files, lines and co-author trailers are not counted.",
        "git:HEAD:first-parent",
        "Author metadata can differ for the same person or be shared; pair work, co-authorship and offline work are not resolved.",
        "This count is not an individual contribution, effort or authorship estimate."))


def readme(root, files, partial):
    candidates = [f for f in files if "/" not in f and re.fullmatch(r"README(?:\.(?:md|txt|rst|markdown))?", f, re.I)]
    if not candidates:
        return blocked("worktree:README", "README inventory was incomplete.") if partial else absent()
    try:
        data = read_bounded(root, sorted(candidates, key=str.casefold)[0]).decode("utf-8", "strict")
    except (OSError, ValueError):
        return blocked("worktree:README", "README candidate could not be inspected within the bounded text rules.")
    heads = []
    fenced = False
    for line in data.splitlines():
        if re.match(r"^\s*(?:```|~~~)", line):
            fenced = not fenced
        match = re.match(r"^\s{0,3}#{1,6}\s+(.{1,120})$", line)
        if match and not fenced:
            heads.append(match[1].casefold())
    titles = {"purpose": r"\b(purpose|overview|about|introduction)\b", "install": r"\b(install\w*|setup|requirements|getting started)\b",
              "usage": r"\b(usage|how to use|running|run|quick start)\b", "example": r"\b(example[s]?|demo)\b",
              "test": r"\b(test\w*|verification)\b", "license": r"\blicen[cs]e\b",
              "AI-use": r"\b(ai use|ai usage|generative ai|ai disclosure)\b"}
    found = [k for k,p in titles.items() if any(re.search(p, h) for h in heads)]
    return reported(fact(f"Recognized Markdown heading cues: {', '.join(found) or 'none'}. "
                         f"Not recognized by this heuristic: {', '.join(k for k in titles if k not in found) or 'none'}.",
                         "worktree:README", "Other heading styles or information outside recognized headings can be missed.",
                         "A heading cue does not establish usable instructions or completeness."))


def naming(files, partial):
    py = [PurePosixPath(f).stem for f in files if f.endswith(".py")]
    if not py:
        return blocked("worktree:filenames", "Inventory incomplete; no Python filenames observed.") if partial else absent()
    n = sum(not bool(re.fullmatch(r"[a-z_][a-z0-9_]*", x)) for x in py)
    return reported(fact(f"{n}/{len(py)} inspected Python file basenames did not match a lowercase/underscore convention.",
                         "worktree:python-filenames", "Conventions may vary; generated files and externally imposed names can be exceptions."))


def hygiene(files, summary):
    count, links, omitted, creds, builds, complete = summary
    large = sum(sz >= 5_000_000 for sz in files.values())
    return reported(fact(f"Inspected a bounded worktree inventory ({count} entries; "
                         f"{'within entry cap' if complete else 'entry cap reached'}; {omitted} other omissions). "
                         f"Symlinks skipped: {links}; credential-like names skipped: {creds}; "
                         f"common generated/cache directory names skipped: {builds}; "
                         f"regular files at least 5,000,000 bytes: {large}; root .gitignore observed: {'yes' if '.gitignore' in files else 'no'}.",
                         "worktree:metadata", "Worktree observations do not establish a tracked file, a live secret, or a build artifact.",
                         "Credential-like filenames may be ordinary files; contents were not read."))


def delta(root, starter, files, summary):
    if starter is None:
        return absent()
    try:
        base, bstats = inventory(Path(starter))
    except (OSError, ValueError):
        return blocked("comparison:provided-starter", "Starter could not be inventoried safely.")
    if bstats[2] and not bstats[0]:
        return blocked("comparison:provided-starter", "Starter could not be inventoried safely.")
    eligible = lambda names: {n for n in names if not any(s.startswith(".") for s in PurePosixPath(n).parts)}
    left, right = eligible(files), eligible(base)
    shared = left & right
    checked = same = 0
    total_bytes = 0
    for name in sorted(shared):
        sizes = files[name] + base[name]
        if max(files[name], base[name]) > MAX_FILE or total_bytes + sizes > MAX_COMPARE_BYTES:
            continue
        total_bytes += sizes
        try:
            a = read_bounded(root, name)
            b = read_bounded(Path(starter), name)
        except (OSError, ValueError):
            continue
        checked += 1
        same += a == b
    return reported(fact(f"Eligible files by path: target-only {len(left-right)}, starter-only {len(right-left)}, "
                         f"shared {len(shared)}; identical bytes in {same}/{checked} compared shared files; "
                         f"{len(shared)-checked} shared files not compared. "
                         f"Comparable inventory within cap: {'yes' if summary[-1] and not summary[2] and bstats[-1] and not bstats[2] else 'no'}.",
                         "comparison:provided-starter", "Inventory excludes hidden files, unsafe entries and common cache/build directories.",
                         "A different starter version, generated files or moves can change counts; similarity is not attribution."))


def process_log(root, files, rows, state, partial):
    key = "FRICTIONAL.md"
    if key not in files:
        return blocked("worktree:FRICTIONAL.md", "Process log not observed in incomplete inventory.") if partial else absent()
    try:
        data = read_bounded(root, key).decode("utf-8", "strict")
    except (ValueError, OSError):
        return blocked("worktree:FRICTIONAL.md", "Process log could not be inspected within the bounded text rules.")
    ds = set()
    for m in re.finditer(r"(?m)^\s{0,3}(?:#{1,6}\s+)?(\d{4}-\d{2}-\d{2})(?=\s|$)", data):
        try:
            ds.add(dt.date.fromisoformat(m[1]))
        except ValueError:
            continue
    refs = set(re.findall(r"(?i)\b(?:commit|sha)\s*[:=]?\s*`?([a-f0-9]{7,64})(?![a-f0-9])", data))
    ids = [r[0] for r in rows]
    resolved = sum(sum(o.startswith(ref.casefold()) for o in ids) == 1 for ref in refs)
    return reported(fact(f"Bounded log present; {len(ds)} distinct valid date-heading cue(s); "
                         f"{resolved}/{len(refs)} distinct commit-reference cue(s) uniquely matched in the inspected "
                         f"first-parent history ({state}). No log text retained.", "worktree:FRICTIONAL.md",
                         "Unmatched references may point to other branches, rebased history, abbreviation collisions or unavailable objects.",
                         "Log cues are not proof of timing, completeness, authorship or learning."))


def structured(dimension, value, head=None):
    """Validate whole packets before calculating aggregate, non-identifying facts."""
    locator = f"input:{dimension}"
    if dimension == "install-test":
        core = {"attested", "isolated", "outcome", "elapsed_seconds"}
        optional = {"revision", "environment", "receipt_sha256"}
        if not isinstance(value, dict) or not core <= value.keys() <= core | optional:
            raise ValueError("schema")
        row = value
        details = []
        if "revision" in row:
            revision = row["revision"]
            if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
                raise ValueError("revision")
            if head is not None and revision != head:
                return blocked(locator, "Supplied install receipt revision differs from locally observed HEAD.")
            details.append("local revision matched" if head else "supplied revision not checked locally")
        if "environment" in row:
            env = choice(row["environment"], {"posix-container", "isolated-vm", "sandbox"})
            details.append(f"reported environment: {env}")
        if "receipt_sha256" in row:
            digest = row["receipt_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("receipt digest")
            details.append("receipt checksum supplied but no receipt bytes verified")
        elapsed = finite(row["elapsed_seconds"], 0, 86400)
        outcome = choice(row["outcome"], {"passed", "failed", "inconclusive"})
        if not (boolean(row["attested"]) and boolean(row["isolated"])):
            return blocked(locator, "Receipt does not attest to an isolated external run; no install was executed.")
        return reported(fact(f"Supplied receipt states an isolated external install check was {outcome}; "
                             f"reported elapsed seconds: {elapsed:.2f}. No commands executed here."
                             + (f" Metadata: {'; '.join(details)}." if details else ""),
                             locator, "An attested receipt is caller-supplied, not independently verified.",
                             "Environment or documented prerequisites may affect this outcome."))

    if dimension in {"push-timeline", "error-trajectory"}:
        ordered = []
        keys = {"at", "kind"} if dimension == "push-timeline" else {"at", "outcome"}
        for r in items(value, "events"):
            if dimension == "push-timeline":
                r = packet(r, keys)
                ordered.append((timestamp(r["at"]), choice(r["kind"], {"push", "ci"}), None))
            else:
                if not isinstance(r, dict):
                    raise ValueError("schema")
                outcome = choice(r.get("outcome"), {"pass", "fail"})
                r = packet(r, keys | ({"category"} if outcome == "fail" else set()))
                category = choice(r["category"], {"assertion", "build", "config", "dependency",
                                                   "environment", "syntax", "other"}) if outcome == "fail" else None
                ordered.append((timestamp(r["at"]), outcome, category))
        if not ordered:
            return absent()
        ordered.sort(key=lambda r: r[0])
        counts = collections.Counter(r[1] for r in ordered)
        span = (ordered[-1][0]-ordered[0][0]).total_seconds() / 3600
        if dimension == "push-timeline":
            return reported(fact(f"Supplied events: push {counts['push']}, CI {counts['ci']}; elapsed span {span:.2f} h.",
                                 locator, "Events may be incomplete; a CI event is not a push event.",
                                 "Server-arrival timestamp does not establish when work occurred or how long it took."))
        category_counts = collections.Counter(r[2] for r in ordered if r[2] is not None)
        summary = ", ".join(f"{key}: {category_counts[key]}" for key in sorted(category_counts)) or "none"
        failures = [r for r in ordered if r[2] is not None]
        categories_shift = sum(a[2] != b[2] for a,b in zip(failures, failures[1:]) if a[0] != b[0])
        if any(a[0] == b[0] for a,b in zip(failures, failures[1:])):
            order_note = "Category transitions not reported because failure timestamps tie."
        else:
            order_note = f"Category changes between successive observed failures: {categories_shift}."
        return reported(fact(f"Supplied checks: {counts['pass']} pass, {counts['fail']} fail. "
                             f"Failure categories: {summary}. {order_note}",
                             locator, "Coverage, environments and failure grouping can differ between runs.",
                             "The number or sequence of failures does not establish learning or effort."))

    if dimension == "temporal-engagement":
        value, grain = _option(value, dimension, "grain")
        grouped = collections.defaultdict(list)
        periods = []
        for r in items(value, "milestones"):
            r = packet(r, {"start", "end", "difficulty"})
            a, b = timestamp(r["start"]), timestamp(r["end"])
            if b < a:
                raise ValueError("negative period")
            difficulty = choice(r["difficulty"], {"low", "medium", "high"})
            grouped[difficulty].append((b-a).total_seconds()/3600)
            periods.append((a,b))
        periods.sort()
        if any(a[1] > b[0] for a,b in zip(periods, periods[1:])):
            raise ValueError("overlapping periods")
        if not periods:
            return absent()
        text = ", ".join(f"{d}: {len(grouped[d])} interval(s), {sum(grouped[d]):.2f} elapsed h"
                         for d in ("low","medium","high") if d in grouped)
        pairs = [(rank, h) for rank, d in enumerate(("low", "medium", "high"), 1) for h in grouped.get(d, [])]
        r = pearson(pairs)
        if r is not None:
            text += (f"; correlation between difficulty (low 1, medium 2, high 3) and interval hours across "
                     f"{len(pairs)} intervals: r = {r:+.2f} "
                     + ("(item-level times, as in the preprint's Y1; probe items are timeboxed, so the longest "
                        "times are cut off)" if grain == "item" else
                        "(the preprint's Y1 uses item-level time on task; milestone intervals are a coarser stand-in)"))
        label = "timed item intervals" if grain == "item" else "milestone intervals"
        return reported(fact(f"Supplied {label} by difficulty: {text}.", locator,
                             "Ranges include gaps, may omit offline work and are not active engagement or time-on-task.",
                             "Difficulty labels are supplied, not established by the repository."))

    design = None
    if dimension == "scaffolding":
        value, design = _option(value, dimension, "design")
    observations = items(value, "observations")
    grouped = collections.defaultdict(lambda: [0, 0])
    brier = 0.0
    confidence_sum = 0.0
    for r in observations:
        if dimension == "transfer":
            r = packet(r, {"distance", "correct"})
            group = choice(r["distance"], {"near", "far"})
            correct = boolean(r["correct"])
        elif dimension == "calibration":
            r = packet(r, {"confidence", "correct"})
            confidence = finite(r["confidence"], 0, 1)
            correct = boolean(r["correct"])
            group = "all"
            confidence_sum += confidence
            brier += (confidence - int(correct)) ** 2
        elif dimension == "retrieval":
            r = packet(r, {"delay_hours", "correct"})
            h = finite(r["delay_hours"], 0, 87600)
            group = "under 24 h" if h < 24 else "24–167.99 h" if h < 168 else "168 h or more"
            correct = boolean(r["correct"])
        elif dimension == "scaffolding":
            r = packet(r, {"level", "successful"})
            group = choice(r["level"], {"none", "hint", "worked"})
            correct = boolean(r["successful"])
        else:
            raise ValueError("unsupported dimension")
        grouped[group][0] += int(correct)
        grouped[group][1] += 1
    if not observations:
        return absent()
    summary = "; ".join(f"{g}: {v[0]}/{v[1]} marked successful" for g,v in sorted(grouped.items()))
    rate = {g: v[0] / v[1] for g, v in grouped.items() if v[1]}
    if dimension == "calibration":
        n = len(observations)
        summary += (f"; supplied mean confidence {confidence_sum/n:.3f}; mean squared error {brier/n:.3f}; "
                    f"mean confidence minus share correct {confidence_sum/n - rate['all']:+.3f} "
                    f"(positive means more confident than correct)")
    elif dimension == "transfer" and {"near", "far"} <= rate.keys():
        summary += (f"; near minus far {rate['near'] - rate['far']:+.3f}; "
                    f"0.4 x near + 0.6 x far = {0.4 * rate['near'] + 0.6 * rate['far']:.3f} (the preprint's Y3 weighting)")
    elif dimension == "scaffolding" and design == "ladder":
        summary = "; ".join(f"{g}: {grouped[g][0]}/{grouped[g][1]} marked successful among those who reached it"
                            for g in ("none", "hint", "worked") if g in grouped)
        return reported(fact(f"Descriptive hint-ladder observations only: {summary}.", locator,
                             "On a ladder only those who failed with no hint reach the partial hint, so these are "
                             "conditional rates; the preprint's (hint - none) / (worked - none) ratio is not computed.",
                             "Item mix, timing and missing observations can change these counts; no learner diagnosis is made."))
    elif dimension == "scaffolding" and {"none", "hint", "worked"} <= rate.keys() and rate["worked"] != rate["none"]:
        summary += (f"; (hint - none) / (worked - none) = "
                    f"{(rate['hint'] - rate['none']) / (rate['worked'] - rate['none']):.3f} (the preprint's Y7 ratio)")
    return reported(fact(f"Descriptive supplied observations only: {summary}.", locator,
                         "Item mix, instructions, conditions and missing observations can change these aggregates.",
                         "No learner diagnosis, independence assumption or grading conclusion is made."))


def pearson(pairs):
    """Pearson correlation of (x, y) pairs, or None when undefined or fewer than three pairs."""
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


STRUCTURED = frozenset({"push-timeline", "install-test", "error-trajectory", "temporal-engagement",
                        "transfer", "calibration", "retrieval", "scaffolding"})
# Optional packet fields beyond the rows (probes.py checks this before writing them).
PACKET_OPTIONS = {"scaffolding": {"design": ("ladder",)}, "temporal-engagement": {"grain": ("item",)}}


def _option(value, dimension, name):
    """Split an optional packet field off; returns (value without it, option or None)."""
    if isinstance(value, dict) and name in value:
        option = choice(value[name], set(PACKET_OPTIONS[dimension][name]))
        return {k: v for k, v in value.items() if k != name}, option
    return value, None


def collect(root: Path, supplied: dict | None = None, starter: Path | None = None,
            deadline: str | None = None, context: dict | None = None) -> dict:
    """Return explicit results with fixed-shape facts; never run project commands.

    If `context` is a dict, it receives the commit rows, history state, folder scope
    and file inventory for later steps (the optional container run). Those values
    are never part of the returned results.
    """
    root = Path(root)
    if supplied is not None and not isinstance(supplied, dict):
        raise TypeError("supplied must be a dictionary or None")
    supplied = supplied or {}
    out = dict.fromkeys(DIMENSIONS)
    try:
        with directory(root):
            pass
    except (OSError, ValueError):
        for name in DIMENSIONS:
            out[name] = blocked("worktree:root", "Target root is not a readable ordinary directory.")
        return out
    files, summary = inventory(root)
    rows, state = history(root)
    if state in ("partial", "unavailable") and len(rows) < MAX_COMMITS:
        # The built-in reader handles loose objects and simple packs. Ordinary clones and
        # forks often need more (large or delta-heavy packs); read them with git itself,
        # using the same locked-down calls as for a folder inside a course repository.
        from gitcli import folder_history
        try:
            more, more_state = folder_history(root, "")
        except Exception:  # noqa: BLE001 - any failure keeps the built-in result
            more, more_state = [], "unavailable"
        if more_state in ("complete", "partial") and len(more) > len(rows):
            rows, state = more, more_state
    scope = None
    if state == "absent":
        # A student folder inside a course repository has no .git of its own:
        # read the enclosing repository's first-parent commits that touch it.
        from gitcli import find_enclosing, folder_history
        enclosing = find_enclosing(root)
        if enclosing is not None:
            rows, state = folder_history(*enclosing)
            scope = enclosing[1]
    partial = not summary[-1] or bool(summary[2])
    out["commit-history"] = commit_findings(rows, state, deadline)
    out["contribution-distribution"] = contributions(rows, state)
    if scope is not None:
        for name in ("commit-history", "contribution-distribution"):
            for item in out[name]["facts"]:
                if item["locator"] == "git:HEAD:first-parent":
                    item["locator"] = f"git:HEAD:first-parent -- {scope}"
            if out[name]["status"] == "reported":
                out[name]["facts"].append(fact(
                    "Commits counted are those in the enclosing repository that touch this folder.",
                    f"git:HEAD:first-parent -- {scope}",
                    "Work committed from another folder, or moved in from elsewhere, is not attributed to this folder."))
    out["readme-completeness"] = readme(root, files, partial)
    out["naming-conventions"] = naming(files, partial)
    out["repo-hygiene"] = hygiene(files, summary)
    out["template-delta"] = delta(root, starter, files, summary)
    out["process-log"] = process_log(root, files, rows, state, partial)
    for name in STRUCTURED:
        if name not in supplied:
            out[name] = absent()
            continue
        try:
            out[name] = structured(name, supplied[name], rows[0][0] if rows else None)
        except (ValueError, TypeError, OverflowError):
            out[name] = blocked(f"input:{name}", "Supplied observations do not match the bounded structured evidence contract.")
    from course_evidence import collect_course
    out.update(collect_course(root, files, rows, partial))
    if context is not None:
        context.update(rows=rows, state=state, scope=scope, files=files)
    return out
