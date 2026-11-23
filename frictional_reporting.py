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


def validate_report(report):
    """Raise ValueError on an invalid report; return None otherwise.

    Authors/collectors are responsible for limiting evidence to redacted,
    necessary excerpts. A structural validator cannot certify that free text
    contains no confidential information.
    """
    _keys(report, (
        "schema_version", "skill_version", "created_at", "target", "model",
        "dimensions", "limitations",
    ), "report")
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
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
        if os.listdir(fd):
            raise ValueError("output directory must be empty")
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
