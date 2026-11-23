"""Validate and render a STEM project review without evaluating repository content."""

import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = "1.0"
CHECK_STATUSES = ("checked", "passed", "failed", "blocked", "not_assessed", "not_applicable")
SEVERITIES = ("high", "medium", "low", "info")
CONFIDENCES = ("high", "medium", "low")
_INLINE_MARKUP = re.compile(r"([\\`*_{}\[\]()#~])")


class ReportValidationError(ValueError):
    """The report does not satisfy the version 1.0 report contract."""


def _fail(location, message):
    raise ReportValidationError(f"{location}: {message}")


def _json_value(value, location):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail(location, "object keys must be strings")
            _json_value(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _json_value(child, f"{location}[{index}]")
    elif type(value) is float:
        if not math.isfinite(value):
            _fail(location, "must be a finite number")
    elif type(value) is str:
        if any(unicodedata.category(c) == "Cs" for c in value):
            _fail(location, "contains an invalid Unicode surrogate")
    elif value is not None and type(value) not in (bool, int):
        _fail(location, "must be a JSON value")


def _object(value, location):
    if not isinstance(value, dict):
        _fail(location, "expected an object")


def _list(value, location):
    if not isinstance(value, list):
        _fail(location, "expected an array")


def _string(value, location):
    if not isinstance(value, str) or not value.strip():
        _fail(location, "expected a non-blank string")


def _fields(obj, location, *keys):
    _object(obj, location)
    for key in keys:
        _string(obj.get(key), f"{location}.{key}")


def _evidence(value, location, allow_text=False):
    _list(value, location)
    for index, entry in enumerate(value):
        at = f"{location}[{index}]"
        if allow_text and isinstance(entry, str):
            _string(entry, at)
            continue
        _fields(entry, at, "path", "observation")
        line = entry.get("line")
        if line is not None and (type(line) is not int or line < 1):
            _fail(f"{at}.line", "expected a positive integer or null")


def _enum(value, location, accepted):
    if value not in accepted:
        _fail(location, f"expected one of {', '.join(accepted)}")


def _unique_id(row, used, location):
    key = row["id"]
    if key in used:
        _fail(f"{location}.id", "duplicate identifier")
    used.add(key)


def validate_report(report):
    """Raise ReportValidationError on invalid data; accept extra JSON metadata."""
    try:
        _json_value(report, "report")
    except RecursionError:
        _fail("report", "must not be cyclic or excessively nested")
    _object(report, "report")
    if report.get("schema_version") != SCHEMA_VERSION:
        _fail("report.schema_version", f"expected {SCHEMA_VERSION!r}")
    _object(report.get("target"), "report.target")
    for kind in ("checks", "findings"):
        entries = report.get(kind)
        _list(entries, f"report.{kind}")
        used = set()
        for index, entry in enumerate(entries):
            at = f"report.{kind}[{index}]"
            if kind == "checks":
                _fields(entry, at, "id", "title", "status", "detail")
                _enum(entry["status"], f"{at}.status", CHECK_STATUSES)
                _evidence(entry.get("evidence"), f"{at}.evidence", allow_text=True)
            else:
                _fields(entry, at, "id", "title", "severity", "confidence",
                        "why_it_matters", "recommended_action")
                _enum(entry["severity"], f"{at}.severity", SEVERITIES)
                _enum(entry["confidence"], f"{at}.confidence", CONFIDENCES)
                _evidence(entry.get("evidence"), f"{at}.evidence")
                if not entry["evidence"]:
                    _fail(f"{at}.evidence", "expected at least one evidence item")
                acceptance = entry.get("acceptance_checks")
                _list(acceptance, f"{at}.acceptance_checks")
                if not acceptance:
                    _fail(f"{at}.acceptance_checks", "expected at least one acceptance check")
                for number, check in enumerate(acceptance):
                    _string(check, f"{at}.acceptance_checks[{number}]")
            _unique_id(entry, used, at)


def _md(value):
    """Display untrusted report content as text, not active Markdown or HTML."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    value = " ".join(value.split())
    value = "".join(
        f"⟦U+{ord(char):04X}⟧" if unicodedata.category(char) in ("Cc", "Cf") else char
        for char in value
    )
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _INLINE_MARKUP.sub(r"\\\1", value)


def _evidence_lines(entries):
    for entry in entries:
        if isinstance(entry, str):
            yield f"  - {_md(entry)}"
        else:
            location = entry["path"]
            if entry.get("line") is not None:
                location += f":{entry['line']}"
            yield f"  - {_md(location)}: {_md(entry['observation'])}"


def render_markdown(report):
    """Render the validated common report as human-readable Markdown."""
    validate_report(report)
    lines = ["# STEM project review", "", "## Target", ""]
    for key, value in sorted(report["target"].items()):
        lines.append(f"- **{_md(key)}:** {_md(value)}")
    if not report["target"]:
        lines.append("No target details recorded.")
    lines.extend(["", "## At a glance", ""])
    checks = Counter(check["status"] for check in report["checks"])
    findings = Counter(finding["severity"] for finding in report["findings"])
    check_counts = ", ".join(f"{checks[status]} {_md(status)}"
                             for status in CHECK_STATUSES if checks[status])
    finding_counts = ", ".join(f"{findings[severity]} {severity}"
                               for severity in SEVERITIES if findings[severity])
    lines.extend([
        f"- **Checks:** {check_counts or 'none recorded'}",
        f"- **Findings:** {finding_counts or 'none recorded'}",
        "- A checked item is not a passing test. Blocked and unassessed items have no pass/fail result.",
        "", "## Checks", "",
    ])
    for check in report["checks"]:
        lines.extend([
            f"### {_md(check['title'])} ({_md(check['id'])})", "",
            f"- **Status:** {_md(check['status'])}",
            f"- **Detail:** {_md(check['detail'])}",
        ])
        if check["evidence"]:
            lines.append("- **Evidence:**")
            lines.extend(_evidence_lines(check["evidence"]))
        lines.append("")
    if not report["checks"]:
        lines.extend(["No checks recorded.", ""])
    lines.extend(["## Findings", ""])
    for finding in report["findings"]:
        lines.extend([
            f"### {_md(finding['title'])} ({_md(finding['id'])})", "",
            f"- **Severity:** {_md(finding['severity'])}",
            f"- **Confidence:** {_md(finding['confidence'])}",
            f"- **Why it matters:** {_md(finding['why_it_matters'])}",
            f"- **Recommended action:** {_md(finding['recommended_action'])}",
        ])
        if finding["evidence"]:
            lines.append("- **Evidence:**")
            lines.extend(_evidence_lines(finding["evidence"]))
        if finding["acceptance_checks"]:
            lines.append("- **Acceptance checks:**")
            lines.extend(f"  - {_md(check)}" for check in finding["acceptance_checks"])
        lines.append("")
    if not report["findings"]:
        lines.extend(["No findings recorded. This does not imply that unassessed areas passed.", ""])
    extra = sorted(set(report) - {"schema_version", "target", "checks", "findings"})
    if extra:
        lines.extend(["## Additional report metadata", ""])
        lines.extend(f"- **{_md(key)}:** {_md(report[key])}" for key in extra)
        lines.append("")
    lines.extend([f"Report schema: {SCHEMA_VERSION}", ""])
    return "\n".join(lines)


def _replace_text(destination, data):
    # A same-directory temp file plus replace does not follow destination symlinks.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=destination.parent, delete=False) as file:
            temp = Path(file.name)
            file.write(data)
        os.replace(temp, destination)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def write_reports(report, out: Path):
    """Write report.md and report.json in out; replace each file atomically.

    The caller must select an output directory outside the repository under review.
    """
    markdown = render_markdown(report)
    raw_json = json.dumps(report, ensure_ascii=False, sort_keys=True,
                          indent=2, allow_nan=False) + "\n"
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    _replace_text(out / "report.md", markdown)
    _replace_text(out / "report.json", raw_json)
