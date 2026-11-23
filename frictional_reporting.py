"""Validate and render the Frictional v1 report; never execute assessed artifacts."""

import json
import os
from pathlib import Path
import re
import stat


STATUSES = {
    "reported": "Reported",
    "no_evidence": "No evidence in the supplied material",
    "blocked": "Blocked",
    "not_applicable": "Not applicable",
    "not_assessed": "Not assessed",
    "hidden_by_instructor": "Hidden by instructor",
}
FAMILIES = {"artifact", "process"}
_NOTICE = (
    "Missing or inaccessible evidence does not imply an absence of learning. "
    "This report does not assign grades or scores."
)
_ESCAPE = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~&:])")
_UNSAFE_KEYS = re.compile(
    r"(?:password|secret|credential|token|authorization|api[_-]?key|"
    r"raw[_-]?(?:log|output|transcript)|(?:stdout|stderr))", re.I
)


def _keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} has missing or unexpected fields")


def _str(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _strings(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of strings")
    for item in value:
        _str(item, label)


def _check_target(value):
    """Reject obvious credential/log fields without exposing rejected data."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _UNSAFE_KEYS.search(key):
                raise ValueError("target metadata contains an invalid or sensitive field")
            _check_target(item)
    elif isinstance(value, list):
        for item in value:
            _check_target(item)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("target metadata must be JSON-compatible")


V1_KEYS = ("schema_version", "skill_version", "created_at", "target", "model", "dimensions", "limitations")
V2_KEYS = V1_KEYS + ("mode", "glp", "next_steps", "run")
GLP_STATES = {
    "evidence_recorded": "Evidence recorded",
    "no_evidence": "No evidence in this project",
    "needs_course_data": "Needs course data (not in a code repository)",
}
PRIORITIES = {"P0": "P0 — fix first: it does not install or run", "P1": "P1 — core evidence missing or failing",
              "P2": "P2 — clarity and traceability"}


def _validate_v2(report):
    if report["mode"] not in ("student", "instructor"):
        raise ValueError("mode must be student or instructor")
    if not isinstance(report["glp"], list) or len(report["glp"]) != 7:
        raise ValueError("glp must list the seven components")
    for row in report["glp"]:
        _keys(row, ("id", "name", "status", "sources", "note"), "glp row")
        for key in ("id", "name", "note"):
            _str(row[key], f"glp.{key}")
        if row["status"] not in GLP_STATES:
            raise ValueError("unrecognized glp status")
        if not isinstance(row["sources"], list):
            raise ValueError("glp.sources must be a list")
        for src in row["sources"]:
            _keys(src, ("id", "status"), "glp source")
            _str(src["id"], "glp source id")
            if src["status"] not in STATUSES:
                raise ValueError("unrecognized glp source status")
    if not isinstance(report["next_steps"], list):
        raise ValueError("next_steps must be a list")
    for step in report["next_steps"]:
        _keys(step, ("priority", "dimension", "locator", "change", "check"), "next step")
        if step["priority"] not in PRIORITIES:
            raise ValueError("unrecognized priority")
        for key in ("dimension", "locator", "change", "check"):
            _str(step[key], f"next_step.{key}")
    run = report["run"]
    if run is not None:
        _keys(run, ("runtime", "image", "outcomes", "receipt", "spec_source"), "run")
        for key in ("runtime", "image", "receipt", "spec_source"):
            _str(run[key], f"run.{key}")
        if not isinstance(run["outcomes"], dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in run["outcomes"].items()):
            raise ValueError("run.outcomes must map phases to outcomes")


def validate_report(report):
    """Raise ValueError on an invalid report; return None otherwise.

    Accepts schema version 1 (the original report) and 2 (adds mode, the GLP
    component table, prioritized next steps and an optional container-run summary).
    Authors/collectors are responsible for limiting evidence to redacted,
    necessary excerpts. A structural validator cannot certify that free text
    contains no confidential information.
    """
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int:
        raise ValueError("schema_version must be 1 or 2")
    version = report["schema_version"]
    if version not in (1, 2):
        raise ValueError("schema_version must be 1 or 2")
    _keys(report, V1_KEYS if version == 1 else V2_KEYS, "report")
    if version == 2:
        _validate_v2(report)
    for key in ("skill_version", "created_at"):
        _str(report[key], key)
    if not isinstance(report["target"], dict):
        raise ValueError("target must be an object")
    _check_target(report["target"])
    _keys(report["model"], ("name", "version"), "model")
    for key in ("name", "version"):
        if report["model"][key] is not None:
            _str(report["model"][key], f"model.{key}")
    _strings(report["limitations"], "limitations")
    if not isinstance(report["dimensions"], list):
        raise ValueError("dimensions must be a list")
    dimension_ids = set()
    for dim in report["dimensions"]:
        _keys(dim, ("id", "family", "version", "status", "facts", "interpretations"), "dimension")
        _str(dim["id"], "dimension.id")
        if dim["id"] in dimension_ids:
            raise ValueError("duplicate dimension id")
        dimension_ids.add(dim["id"])
        if type(dim["version"]) is not int or dim["version"] != 1:
            raise ValueError("dimension.version must be 1")
        if not isinstance(dim["family"], str) or dim["family"] not in FAMILIES:
            raise ValueError("unrecognized dimension family")
        if not isinstance(dim["status"], str) or dim["status"] not in STATUSES:
            raise ValueError("unrecognized dimension status")
        for field in ("facts", "interpretations"):
            entries = dim[field]
            if not isinstance(entries, list):
                raise ValueError(f"{field} must be a list")
            if entries and dim["status"] != "reported":
                raise ValueError("only a reported dimension can include evidence")
            if entries and field == "interpretations" and not all(report["model"].values()):
                raise ValueError("interpretations require both model name and version")
            for entry in entries:
                _keys(entry, ("observation", "locator", "alternative_explanations"), "evidence item")
                for key in ("observation", "locator"):
                    _str(entry[key], f"evidence.{key}")
                _strings(entry["alternative_explanations"], "alternative_explanations")
    try:
        json.dumps(report, allow_nan=False).encode("utf-8")
    except (TypeError, OverflowError, ValueError, UnicodeError, RecursionError):
        raise ValueError("report must be valid UTF-8 JSON") from None


def _md(value):
    # Force every untrusted value onto one rendered line and neutralize
    # Markdown/HTML punctuation and invisible control/bidi characters.
    value = "".join(c if c.isprintable() else " " for c in value)
    return _ESCAPE.sub(r"\\\1", " ".join(value.split()))


def _section(lines, dimensions, key):
    for dim in dimensions:
        if not dim[key]:
            continue
        lines.extend([f"### {_md(dim['id'])}", ""])
        for item in dim[key]:
            lines.append(f"- {_md(item['observation'])} (locator: {_md(item['locator'])})")
            for explanation in item["alternative_explanations"]:
                lines.append(f"  - Alternative explanation: {_md(explanation)}")
        lines.append("")
    if not any(dim[key] for dim in dimensions):
        lines.extend(["None recorded.", ""])


def _render_v2(report):
    lines = ["", "## Next steps", ""]
    steps = report["next_steps"]
    if not steps:
        lines.append("No next steps follow from what this audit could check. That is not a verdict on the work.")
    for level in PRIORITIES:
        group = [s for s in steps if s["priority"] == level]
        if not group:
            continue
        lines.extend([f"### {PRIORITIES[level]}", ""])
        for s in group:
            lines.append(f"- {_md(s['change'])} (where: {_md(s['locator'])}; from: {_md(s['dimension'])})")
            lines.append(f"  - How you will know it worked: {_md(s['check'])}")
        lines.append("")
    run = report["run"]
    lines.extend(["", "## Container run", ""])
    if run is None:
        lines.append("Not run. Add --run to install and run the project in a fresh Linux container.")
    else:
        outcomes = ", ".join(f"{_md(k)}: {_md(v)}" for k, v in sorted(run["outcomes"].items()))
        lines.append(f"Ran in {_md(run['runtime'])} with image {_md(run['image'])} ({_md(run['spec_source'])}). "
                     f"Outcomes: {outcomes or 'none recorded'}. Records: {_md(run['receipt'])}.")
    lines.extend(["", "## Frictional components (GLP)", "",
                  "Evidence for a component is not a measure of it. No GLP score is computed.", "",
                  "| Component | Status | Sources checked | Note |", "|---|---|---|---|"])
    for row in report["glp"]:
        sources = "; ".join(f"{_md(s['id'])}: {STATUSES[s['status']]}" for s in row["sources"])
        lines.append(f"| {_md(row['id'])} {_md(row['name'])} | {GLP_STATES[row['status']]} | {sources} | {_md(row['note'])} |")
    return lines


def _render(report):
    dimensions = report["dimensions"]
    lines = [
        "# Frictional review", "", _NOTICE, "",
        f"Created: {_md(report['created_at'])}",
        f"Skill version: {_md(report['skill_version'])}", "",
    ]
    target = report["target"]
    if target.get("kind") in ("folder", "github"):
        lines.append(f"Target kind: {target['kind']}")
    revision = target.get("revision")
    if isinstance(revision, str) and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision):
        lines.append("Recorded Git revision: `" + revision + "`")
    if type(target.get("inventoried_files")) is int:
        lines.append(f"Files seen in bounded scan: {target['inventoried_files']}")
    if isinstance(target.get("snapshot"), str):
        lines.append(f"Snapshot scope: {_md(target['snapshot'])}")
    if report.get("mode"):
        lines.append(f"Report for: {'the student' if report['mode'] == 'student' else 'the instructor (shareable with the student; private notes are in instructor-notes.md)'}")
    if report.get("schema_version") == 2:
        lines.extend(_render_v2(report))
    lines.extend(["", "## Dimension status", ""])
    lines.extend(f"- {_md(d['id'])} ({d['family']}): {STATUSES[d['status']]}" for d in dimensions)
    if not dimensions:
        lines.append("No dimensions recorded.")
    lines.extend(["", "## Observed facts", ""])
    _section(lines, dimensions, "facts")
    lines.extend(["## Model-assisted interpretations (optional; not facts)", ""])
    if any(d["interpretations"] for d in dimensions):
        lines.extend([
            "Model: " + _md(report["model"]["name"]) + " (" + _md(report["model"]["version"]) + ")",
            "",
        ])
    _section(lines, dimensions, "interpretations")
    lines.extend(["## Limitations", ""])
    lines.extend(f"- {_md(item)}" for item in report["limitations"])
    if not report["limitations"]:
        lines.append("None recorded.")
    return "\n".join(lines) + "\n"


def write_reports(report, out: Path):
    """Write report.json and report.md to a new or empty output directory.

    Existing contents are not replaced. The caller chooses an output directory
    outside the assessed target; its parent must exist.
    """
    validate_report(report)
    contents = {
        "report.json": (json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8"),
        "report.md": _render(report).encode("utf-8"),
    }
    out = Path(out)
    try:
        out.mkdir(mode=0o700)
    except FileExistsError:
        if not stat.S_ISDIR(out.lstat().st_mode):
            raise ValueError("output must be a new or empty directory") from None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(out, flags)
    created = []
    try:
        if set(os.listdir(fd)) - {"run"}:
            raise ValueError("output directory must be empty (apart from run records)")
        for name, data in contents.items():
            f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            created.append(name)
            with os.fdopen(f, "wb") as stream:
                stream.write(data)
    except BaseException:
        for name in created:
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
        raise
    finally:
        os.close(fd)
    return out / "report.json", out / "report.md"


def write_instructor_notes(report, notes, out: Path):
    """instructor-notes.md: line-level pointers for grading the Frictional rows and questions to ask.

    Kept out of report.md and report.json so the report can be shared with the
    student. Contains locators and dates only, no student text.
    """
    lines = ["# Instructor notes", "",
             "Private to the instructor. The report beside this file can be shared with the student; this file should not be.",
             "Nothing here is a grade. It points to where to look when you apply the Frictional rubric (2 specific, 1 vague, 0 absent per row).", ""]
    lines.extend(["## Where to look", ""])
    lines.extend(f"- {_md(n)}" for n in notes) if notes else lines.append("No line-level pointers.")
    lines.extend(["", "## Components with no evidence in this project", ""])
    gaps = [r for r in report.get("glp", []) if r["status"] != "evidence_recorded"]
    lines.extend(f"- {_md(r['id'])} {_md(r['name'])}: {GLP_STATES[r['status']]}. {_md(r['note'])}" for r in gaps) \
        if gaps else lines.append("Every component has some recorded evidence.")
    lines.extend(["", "## Questions to ask", "",
                  "- Walk me through one entry in FRICTIONAL.md where something went wrong: what did you try next, and why?",
                  "- Pick one prediction you made: what did you expect, what happened, and what does the difference tell you?",
                  "- Which part of this code could you change without help right now? Show me."])
    data = ("\n".join(lines) + "\n").encode("utf-8")
    fd = os.open(Path(out) / "instructor-notes.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
