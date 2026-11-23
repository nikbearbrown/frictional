"""Course process files: FRICTIONAL.md, PREDICTIONS.md, VERIFICATION.md, CONTRIBUTIONS.md.

Professor Bear's courses ask students to keep these four files from the course
templates. This module reads them with the same bounded, no-follow file access as
process_evidence and reports fixed-phrase counts. It never copies a student's
sentences into a report: facts carry counts, dates and line numbers only.

What a script can say: which template fields are filled, which entries are dated,
whether dates line up with commits, and the arithmetic of numeric predictions.
What it cannot say: whether an entry is specific or vague, honest, or shows real
friction. Those are a reader's judgments (the course rubric's 2/1/0), recorded
separately as labelled interpretations, never as scores.

collect_course(root, files, rows) returns {dimension: result} for COURSE_DIMENSIONS.
A result may also carry "next_steps" (for the student report) and "notes"
(instructor-only, never written into the student-facing report).
"""

from __future__ import annotations

import datetime as dt
from pathlib import PurePosixPath
import re

from process_evidence import absent, blocked, fact, read_bounded, reported

COURSE_DIMENSIONS = (
    "log-rubric-rows", "log-commit-consistency", "prediction-log",
    "verification-notes", "contributions-log",
)
import process_evidence as _pe
assert set(COURSE_DIMENSIONS) <= set(_pe.DIMENSIONS)
MAX_DEPTH = 4
MAX_FILES_PER_KIND = 20

LABEL = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*|__)?([A-Za-z][^:\n]{1,90}?)(?:\*\*|__)?\s*:(?:\*\*|__)?\s*(.*)$")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
COMMIT = re.compile(r"(?i)(?:\bcommit\b|\bsha\b)\s*[:=#]?\s*`?([0-9a-f]{7,64})\b|`([0-9a-f]{7,40})`|\b([0-9a-f]{40})\b")
PLACEHOLDER = re.compile(r"^(?:n/?a|tbd|todo|tba|-+|\.+|…|\?+|xx+|\[.*\]|<.*>)$", re.I)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
ISO_DATE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
NAMED_DATE = re.compile(
    r"\b(?:(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(20\d{2})|([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2}))\b")

# FRICTIONAL.md template labels, matched by meaning so small wording changes still count.
LOG_FIELDS = (
    ("evidence", re.compile(r"(?i)evidence|commit")),
    ("date", re.compile(r"(?i)^date\b")),
    ("next", re.compile(r"(?i)did next|then did|my response|what i changed")),
    ("contrib", re.compile(r"(?i)claude|another person|contribut|\bai\b|assist|other people")),
    ("learning", re.compile(r"(?i)understand|learn|uncertain|unresolved|still do not")),
    ("friction", re.compile(r"(?i)happened|difficult|checked|surpris|confus|friction|struggle|stuck")),
    ("attempts", re.compile(r"(?i)tried|expected|attempt")),
)
ROWS = (
    ("Attempts", ("attempts",)),
    ("Friction and response", ("friction",)),
    ("Human and AI contributions", ("contrib",)),
    ("Learning and uncertainty", ("learning",)),
    ("Traceable process", ()),  # computed: date plus evidence/commit/link
)
VERIFY_FIELDS = (
    ("candidate", re.compile(r"(?i)candidate|content identifier")),
    ("inputs", re.compile(r"(?i)input sources|fixture|simulation")),
    ("environment", re.compile(r"(?i)environment|exact commands")),
    ("criteria", re.compile(r"(?i)acceptance criteria")),
    ("expected", re.compile(r"(?i)expected result")),
    ("observed", re.compile(r"(?i)observed result")),
    ("failed", re.compile(r"(?i)checks that failed|subsequent revision|failed")),
    ("assumptions", re.compile(r"(?i)shared assumptions|common errors")),
    ("limits", re.compile(r"(?i)remaining limits|limits")),
    ("reviewer", re.compile(r"(?i)reviewer|automated checker")),
    ("handoff", re.compile(r"(?i)handoff|owner")),
)
CONTRIB_FIELDS = (
    ("sources", re.compile(r"(?i)starting sources|licen")),
    ("personal", re.compile(r"(?i)personally|what i (?:attempted|built)")),
    ("claude", re.compile(r"(?i)claude contributed|what claude|model identifier|ai contributed")),
    ("decisions", re.compile(r"(?i)accepted|changed, or rejected|rejected")),
    ("people", re.compile(r"(?i)other people")),
    ("explain", re.compile(r"(?i)can explain|remains unclear")),
    ("evidence", re.compile(r"(?i)evidence links|commit references|evidence")),
)
PRED_FIELDS = (
    ("held", re.compile(r"(?i)prediction held|held\b|was i right|correct\?")),
    ("difference", re.compile(r"(?i)difference")),
    ("actual", re.compile(r"(?i)actual")),
    ("revised", re.compile(r"(?i)revised|remaining uncertainty")),
    ("confidence", re.compile(r"(?i)confiden")),
    ("expected", re.compile(r"(?i)expected")),
    ("question", re.compile(r"(?i)question")),
    ("date", re.compile(r"(?i)^date\b")),
)
MODEL_NAME = re.compile(r"(?i)\b(claude|opus|sonnet|haiku|fable|gpt|chatgpt|o\d|gemini|copilot|codex|llama|mistral|deepseek|qwen)\b[\w .:/-]{0,30}")


def _find(files, name):
    out = []
    for path in sorted(files):
        p = PurePosixPath(path)
        if p.name.lower() != name.lower() or len(p.parts) > MAX_DEPTH:
            continue
        if any(part.lower() in {"templates", "template", "starter"} for part in p.parts[:-1]):
            continue
        out.append(path)
    return out[:MAX_FILES_PER_KIND]


def _read_lines(root, path):
    data = read_bounded(root, path)
    return data.decode("utf-8", "strict").splitlines()


def parse_date(text):
    """First recognisable calendar date in text, or None. Never guesses a year."""
    for rx, kind in ((ISO_DATE, "iso"), (US_DATE, "us"), (NAMED_DATE, "named")):
        m = rx.search(text)
        if not m:
            continue
        try:
            if kind == "iso":
                return dt.date(int(m[1]), int(m[2]), int(m[3]))
            if kind == "us":
                return dt.date(int(m[3]), int(m[1]), int(m[2]))
            if m[1]:
                day, month, year = m[1], m[2], m[3]
            else:
                month, day, year = m[4], m[5], m[6]
            number = MONTHS.get(month[:3].lower())
            if number:
                return dt.date(int(year), number, int(day))
        except ValueError:
            continue
    return None


def _classify(label, table):
    for key, rx in table:
        if rx.search(label):
            return key
    return None


def _filled(value):
    text = " ".join(value.split())
    if not text or PLACEHOLDER.fullmatch(text):
        return False
    return len(re.findall(r"[A-Za-z0-9]+", text)) >= 2 or text.lower() in {"none", "no", "yes"}


def _fields(lines, table, start=0):
    """Map field key -> (text, line number) for template-style 'Label: value' blocks."""
    fields, current = {}, None
    for offset, line in enumerate(lines):
        number = start + offset + 1
        if HEADING.match(line):
            current = None
            continue
        m = LABEL.match(line)
        key = _classify(m[1], table) if m else None
        if key is not None:
            if key in fields and _filled(fields[key][0]):
                current = None  # a repeated label starts a new block; caller splits entries
                continue
            fields[key] = [m[2], number]
            current = key
        elif current is not None and line.strip():
            fields[current][0] += " " + line.strip()
    return {k: (v[0], v[1]) for k, v in fields.items()}


def _entries(lines):
    """Split a FRICTIONAL.md into entries: by headings, and by repeated Date labels."""
    starts = []
    for i, line in enumerate(lines):
        h = HEADING.match(line)
        if h:
            starts.append(i)
            continue
        m = LABEL.match(line)
        if m and _classify(m[1], LOG_FIELDS) == "date":
            prev = starts[-1] if starts else None
            between = lines[prev + 1:i] if prev is not None else lines[:i]
            if prev is None or any(LABEL.match(x) and _classify(LABEL.match(x)[1], LOG_FIELDS) not in (None, "date")
                                   for x in between):
                starts.append(i)
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    out = []
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        block = lines[a:b]
        fields = _fields(block, LOG_FIELDS, a)
        heading = HEADING.match(block[0]) if block else None
        text = "\n".join(block)
        date = None
        if "date" in fields:
            date = parse_date(fields["date"][0])
        if date is None and heading:
            date = parse_date(heading[2])
        labelled = len([k for k in fields if k != "date"]) >= 2
        if not labelled and date is None:
            continue  # preamble, title or notes that are not an entry
        if not labelled and not any(x.strip() for x in block[1:]):
            continue  # an empty dated heading
        out.append({
            "line": a + 1,
            "date": date,
            "labelled": labelled,
            "fields": fields,
            "retrospective": bool(re.search(r"(?i)retrospect", re.sub(r"(?i)\(\s*mark retrospective entries\s*\)", "", text))),
            "commits": {next(g for g in m.groups() if g) for m in COMMIT.finditer(text)},
            "links": bool(re.search(r"https?://|\b[\w./-]+\.(?:py|md|js|ts|ipynb|json|csv|gd|tscn|html|txt)\b", text)),
        })
    return out


def _row_filled(entry, row):
    name, keys = row
    f = entry["fields"]
    if name == "Traceable process":
        evidence = "evidence" in f and _filled(f["evidence"][0])
        return entry["date"] is not None and (evidence or bool(entry["commits"]) or entry["links"])
    return any(k in f and _filled(f[k][0]) for k in keys)


def _log_rows(root, paths, commit_ids):
    facts, steps, notes = [], [], []
    for path in paths:
        try:
            entries = _entries(_read_lines(root, path))
        except (OSError, ValueError, UnicodeError):
            facts.append(fact("Log could not be read within the bounded text rules.", path))
            continue
        labelled = [e for e in entries if e["labelled"]]
        dated = sum(e["date"] is not None for e in entries)
        retro = sum(e["retrospective"] for e in entries)
        counts = []
        for row in ROWS:
            pool = entries if row[0] == "Traceable process" else labelled
            have = [e for e in pool if _row_filled(e, row)]
            counts.append(f"{row[0]} {len(have)}/{len(pool)}")
            missing = [e for e in pool if e not in have]
            if missing and pool:
                where = ", ".join(f"line {e['line']}" for e in missing[:8]) + (" …" if len(missing) > 8 else "")
                notes.append(f"{path}: {row[0]} not filled in {len(missing)} of {len(pool)} entries ({where}).")
                steps.append({"priority": "P2", "dimension": "log-rubric-rows", "locator": path,
                              "change": f"Fill in “{row[0]}” in the {len(missing)} entr{'y that leaves' if len(missing) == 1 else 'ies that leave'} "
                                        f"it blank ({where}). Say what actually happened; an unsuccessful attempt counts.",
                              "check": f"Rerun the audit: log-rubric-rows shows {row[0]} {len(pool)}/{len(pool)}."})
        with_response = sum(1 for e in labelled if "next" in e["fields"] and _filled(e["fields"]["next"][0]))
        resolved = {c for e in entries for c in e["commits"]}
        matched = sum(1 for c in resolved if sum(i.startswith(c.lower()) for i in commit_ids) == 1)
        facts.append(fact(
            f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} ({dated} dated, {retro} marked retrospective, "
            f"{len(entries) - len(labelled)} without template labels). Template rows filled: "
            + "; ".join(counts) + f". Entries naming what was done next: {with_response}/{len(labelled)}. "
            f"Commit references: {len(resolved)}, {matched} found in this folder's history. No log text retained.",
            path,
            "A filled field is not necessarily a specific one; the course rubric's specific (2), vague (1), absent (0) judgment needs a reader.",
            "Entries without template labels can still meet the rubric; a reader has to judge them."))
        if not entries:
            steps.append({"priority": "P1", "dimension": "log-rubric-rows", "locator": path,
                          "change": "Add dated entries using the course template (date, what you tried and expected, what happened, what you did next, who contributed what, what you understand now, evidence).",
                          "check": "Rerun the audit: log-rubric-rows counts at least one dated entry."})
    return facts, steps, notes


def _consistency(root, paths, rows):
    """Facts and instructor notes comparing log dates with commit days (one-day tolerance)."""
    if not rows:
        return [], []
    commit_days = sorted({r[1].date() for r in rows})
    near = lambda d, pool: any(abs((d - x).days) <= 1 for x in pool)
    facts, notes = [], []
    for path in paths:
        try:
            entries = _entries(_read_lines(root, path))
        except (OSError, ValueError, UnicodeError):
            continue
        days = sorted({e["date"] for e in entries if e["date"] is not None})
        if not days:
            continue
        logged_near_commit = [d for d in days if near(d, commit_days)]
        commits_near_log = [d for d in commit_days if near(d, days)]
        quiet_commit_days = [d for d in commit_days if not near(d, days)]
        quiet_log_days = [d for d in days if not near(d, commit_days)]
        facts.append(fact(
            f"Log dates within one day of a commit to this folder: {len(logged_near_commit)}/{len(days)}. "
            f"Commit days within one day of a log entry: {len(commits_near_log)}/{len(commit_days)}. "
            f"Span of log dates {days[0].isoformat()} to {days[-1].isoformat()}; "
            f"span of commit days {commit_days[0].isoformat()} to {commit_days[-1].isoformat()}.",
            path,
            "Work happens without commits, and commits happen without log-worthy events.",
            "Commit dates are editable and use UTC here, which can shift a date by a day."))
        if quiet_commit_days:
            notes.append(f"{path}: ask about commit days with no log entry within a day: "
                         + ", ".join(d.isoformat() for d in quiet_commit_days[:10]) + ".")
        if quiet_log_days:
            notes.append(f"{path}: log dates with no commit within a day: "
                         + ", ".join(d.isoformat() for d in quiet_log_days[:10]) + ".")
    return facts, notes


def _confidence(value):
    text = value.lower()
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if m and 0 <= float(m[1]) <= 100:
        return float(m[1]) / 100, True
    m = re.search(r"\b(\d{1,2})\s*/\s*10\b", text)
    if m and 0 <= int(m[1]) <= 10:
        return int(m[1]) / 10, True
    m = re.search(r"(?<![\d.])(0?\.\d+|1\.0+)(?![\d.%/])", text)
    if m:
        return float(m[1]), True
    return None, bool(re.search(r"[a-z]", text))


def _held(value):
    text = value.strip().lower()
    if re.match(r"(?:yes|correct|right|held|it held)\b", text):
        return 1.0
    if re.match(r"(?:no|incorrect|wrong|did not|didn't|not held)\b", text):
        return 0.0
    if re.match(r"(?:partly|partially|somewhat|mixed)\b", text):
        return 0.5
    return None


def _predictions(root, paths):
    facts, steps = [], []
    for path in paths:
        try:
            lines = _read_lines(root, path)
        except (OSError, ValueError, UnicodeError):
            facts.append(fact("Prediction log could not be read within the bounded text rules.", path))
            continue
        blocks, current = [], None
        for i, line in enumerate(lines):
            h = HEADING.match(line)
            if h and re.search(r"(?i)before|prediction\b|^\s*\d{4}-\d{2}-\d{2}", h[2]) and not re.search(r"(?i)after", h[2]):
                current = {"start": i, "after": None}
                blocks.append(current)
            elif h and re.search(r"(?i)after", h[2]) and current is not None:
                current["after"] = i
        if not blocks:
            blocks = [{"start": 0, "after": None}]
        rows = []
        for n, b in enumerate(blocks):
            end = blocks[n + 1]["start"] if n + 1 < len(blocks) else len(lines)
            fields = _fields(lines[b["start"]:end], PRED_FIELDS, b["start"])
            conf, stated = _confidence(fields["confidence"][0]) if "confidence" in fields else (None, False)
            held = _held(fields["held"][0]) if "held" in fields else None
            after = any(k in fields and _filled(fields[k][0]) for k in ("actual", "difference", "revised", "held"))
            expected = "expected" in fields and _filled(fields["expected"][0])
            if expected or stated or after:
                rows.append((conf, stated, held, after, b["start"] + 1))
        if not rows:
            facts.append(fact("No filled prediction found (no expected result, confidence or after-run notes).", path,
                              "Predictions may be written elsewhere, for example inside FRICTIONAL.md."))
            continue
        numeric = [r for r in rows if r[0] is not None]
        stated = [r for r in rows if r[1]]
        after = [r for r in rows if r[3]]
        marked = [r for r in rows if r[2] is not None]
        text = (f"{len(rows)} prediction(s). Confidence stated in {len(stated)} ({len(numeric)} as a number). "
                f"After-run notes filled in {len(after)}. Outcome marked (held: yes, partly, no) in {len(marked)}.")
        scored = [r for r in rows if r[0] is not None and r[2] in (0.0, 1.0)]
        if scored:
            n = len(scored)
            mean_c = sum(r[0] for r in scored) / n
            share = sum(r[2] for r in scored) / n
            brier = sum((r[0] - r[2]) ** 2 for r in scored) / n
            text += (f" For {n} prediction(s) with a numeric confidence and a yes/no outcome: mean confidence {mean_c:.2f}, "
                     f"share that held {share:.2f}, mean squared error {brier:.3f} (0 is perfect), "
                     f"confidence minus share held {mean_c - share:+.2f} (positive means more confident than right).")
        facts.append(fact(text, path,
                          "A few predictions are too few to measure calibration; these numbers are descriptive.",
                          "Students choose which predictions to write down, and outcome marks are their own."))
        if stated and not numeric:
            steps.append({"priority": "P2", "dimension": "prediction-log", "locator": path,
                          "change": "Write your confidence as a number before each run (for example “Confidence: 70%”), not only in words.",
                          "check": "Rerun the audit: prediction-log counts confidence as a number."})
        if after and not marked:
            steps.append({"priority": "P2", "dimension": "prediction-log", "locator": path,
                          "change": "After each run, add one line: “Prediction held: yes / partly / no”.",
                          "check": "Rerun the audit: prediction-log reports a mean squared error."})
        if len(after) < len(rows):
            steps.append({"priority": "P2", "dimension": "prediction-log", "locator": path,
                          "change": f"Fill in the after-run section for {len(rows) - len(after)} prediction(s): what actually happened and how it differed.",
                          "check": f"Rerun the audit: after-run notes filled in {len(rows)} of {len(rows)}."})
    return facts, steps


def _template_file(root, paths, table, label, commit_ids, dimension):
    facts, steps = [], []
    for path in paths:
        try:
            fields = _fields(_read_lines(root, path), table)
        except (OSError, ValueError, UnicodeError):
            facts.append(fact(f"{label} could not be read within the bounded text rules.", path))
            continue
        filled = {k for k, (v, _) in fields.items() if _filled(v)}
        total = len(table)
        parts = [f"{len(filled)} of {total} template fields filled"]
        if dimension == "verification-notes":
            for key, words in (("failed", "checks that failed and the revision that followed"),
                               ("environment", "environment and exact commands"),
                               ("criteria", "acceptance criteria fixed before checking"),
                               ("reviewer", "who or what did the checking")):
                parts.append(f"{words}: {'filled' if key in filled else 'blank'}")
            if "candidate" in fields:
                ids = {next(g for g in m.groups() if g) for m in COMMIT.finditer(fields["candidate"][0])}
                ids |= set(re.findall(r"\b[0-9a-f]{7,40}\b", fields["candidate"][0]))
                if ids:
                    found = any(sum(i.startswith(c.lower()) for i in commit_ids) == 1 for c in ids)
                    parts.append("candidate commit " + ("found in this folder's history" if found else "not found in this folder's history"))
            if "failed" not in filled:
                steps.append({"priority": "P2", "dimension": dimension, "locator": path,
                              "change": "Under “Checks that failed and subsequent revision”, record a check that failed and what you changed, or say that none failed and which checks you ran.",
                              "check": "Rerun the audit: verification-notes shows that field filled."})
        if dimension == "contributions-log":
            claude = fields.get("claude", ("", 0))[0]
            parts.append("model named: " + ("yes" if MODEL_NAME.search(claude) else "no"))
            parts.append("accepted, changed or rejected decisions: " + ("filled" if "decisions" in filled else "blank"))
            if "claude" in filled and not MODEL_NAME.search(claude):
                steps.append({"priority": "P2", "dimension": dimension, "locator": path,
                              "change": "Name the AI model as it was displayed (for example the model name and version) where you describe what it contributed.",
                              "check": "Rerun the audit: contributions-log shows “model named: yes”."})
        if total - len(filled):
            steps.append({"priority": "P2", "dimension": dimension, "locator": path,
                          "change": f"{label}: fill in the {total - len(filled)} blank template field(s), or write why one does not apply.",
                          "check": f"Rerun the audit: {dimension} shows {total} of {total} fields filled."})
        facts.append(fact("; ".join(parts) + ". No text retained.", path,
                          "A filled field is not necessarily accurate or specific; a reader judges that.",
                          "The file describes the work; it does not show that checks or contributions happened as described."))
    return facts, steps


def collect_course(root, files, rows, partial=False):
    """Results for COURSE_DIMENSIONS. rows: commit rows for this folder (may be empty)."""
    commit_ids = [r[0] for r in rows]
    out = {}

    def finish(name, facts, steps=(), notes=()):
        if facts:
            res = reported(*facts)
        else:
            res = blocked(f"worktree:{name}", "Inventory incomplete; file not observed.") if partial else absent()
        if steps:
            res["next_steps"] = list(steps)
        if notes:
            res["notes"] = list(notes)
        out[name] = res

    logs = _find(files, "FRICTIONAL.md")
    facts, steps, notes = _log_rows(root, logs, commit_ids) if logs else ([], [], [])
    finish("log-rubric-rows", facts, steps, notes)

    facts, notes = _consistency(root, logs, rows) if logs else ([], [])
    finish("log-commit-consistency", facts, (), notes)

    preds = _find(files, "PREDICTIONS.md")
    facts, steps = _predictions(root, preds) if preds else ([], [])
    finish("prediction-log", facts, steps)

    ver = _find(files, "VERIFICATION.md")
    facts, steps = _template_file(root, ver, VERIFY_FIELDS, "VERIFICATION.md", commit_ids, "verification-notes") if ver else ([], [])
    finish("verification-notes", facts, steps)

    con = _find(files, "CONTRIBUTIONS.md")
    facts, steps = _template_file(root, con, CONTRIB_FIELDS, "CONTRIBUTIONS.md", commit_ids, "contributions-log") if con else ([], [])
    finish("contributions-log", facts, steps)
    return out
