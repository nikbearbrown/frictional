"""Read-only Frictional audit: static STEM baseline and separately sourced process evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from frictional_reporting import validate_report, write_reports
from stem_project_review import REMOTE, SHA, clone_repo, safe_text, static_audit, within_tree

VERSION = "0.1.0"
MAX_INPUT = 2 * 1024 * 1024
ARTIFACT = {
    "purpose-scope": ("inventory", "documentation"),
    "outside-in-workflow": ("runtime",),
    "scientific-validity": ("scientific_artifacts",),
    "data-evaluation": (),
    "reproducibility": ("dependencies",),
    "correctness": ("code_review",),
    "architecture": (),
    "verification": ("test_inventory",),
    "safety-security": ("operational_readiness",),
    "documentation-licensing": ("licensing",),
}
PROCESS = (
    "commit-history", "push-timeline", "install-test", "readme-completeness",
    "naming-conventions", "repo-hygiene", "template-delta", "contribution-distribution",
    "process-log", "verification-record", "error-trajectory", "temporal-engagement", "transfer",
    "calibration", "retrieval", "scaffolding",
)
LIMITS = [
    "This is a bounded static intake, not a full source review, project execution, or validation of scientific claims.",
    "No target code, tests, or install instructions were executed. An external receipt is supplied evidence, not a reproduced result.",
    "Repository history describes the visible revision and recorded metadata, not the duration of work, authorship, or what a person learned.",
    "Script observations and any opt-in model interpretations are separate. Never use this report to assign a grade or determine misconduct.",
    "Grade and transfer data are not sought automatically. Only pass separately authorized, minimized evidence; an institution's agent may have its own data processing terms.",
]
CONFOUNDS = {
    "purpose-scope": "The project may document its intended outcome outside conventional README or documentation paths.",
    "outside-in-workflow": "Static discovery does not establish that a user can complete the workflow. See install-test for any separate receipt.",
    "scientific-validity": "A result file may be illustrative or stale, and relevant methods may live outside the project.",
    "reproducibility": "A lockfile records declared dependencies, not a compatible runtime, accessible dataset or verified reproduction.",
    "verification": "A named test may not exercise the main claim, and CI files do not show that a run completed.",
    "documentation-licensing": "A conventional filename does not establish that all materials share the same reuse terms.",
}


def read_object(path: Path) -> dict:
    """Only explicit bounded JSON inputs; never follow a symlink at the file."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with os.fdopen(os.open(path.expanduser(), flags), "rb") as source:
            meta = os.fstat(source.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > MAX_INPUT:
                raise ValueError("Expected a regular JSON input no larger than 2 MiB.")
            payload = source.read(MAX_INPUT + 1)
        if len(payload) > MAX_INPUT:
            raise ValueError("JSON input exceeds 2 MiB.")
        def reject_constant(_value):
            raise ValueError("JSON input contains a non-finite number.")
        def distinct(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError("JSON input repeats a field.")
                out[key] = value
            return out
        value = json.loads(payload, parse_constant=reject_constant, object_pairs_hook=distinct)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Cannot read a regular bounded JSON input.") from exc
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def hidden_ids(path: Path | None) -> set[str]:
    if path is None:
        return set()
    data = read_object(path)
    hide = data.get("hide")
    if not isinstance(hide, list) or any(not isinstance(x, str) or x not in (*ARTIFACT, *PROCESS) for x in hide):
        raise ValueError("Profile 'hide' must list known dimension IDs.")
    return set(hide)


def fact(observation: str, locator: str, alternative: str) -> dict:
    return {"observation": observation, "locator": locator, "alternative_explanations": [alternative]}


RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


def verification_record(packet, revision: str | None) -> dict:
    """Validate normalized assertions about historical verification, not authorship."""
    absent = {"status": "no_evidence", "facts": []}
    if packet is None:
        return absent
    def reject():
        raise ValueError("Invalid verification-record packet; see the documented JSON contract.")
    if not isinstance(packet, dict) or set(packet) != {"claimed_run", "runs"}:
        reject()
    claim, runs = packet["claimed_run"], packet["runs"]
    if claim is not None and (not isinstance(claim, str) or not RUN_ID.fullmatch(claim)):
        reject()
    if not isinstance(runs, list) or len(runs) > 50:
        reject()
    index = {}
    for run in runs:
        if not isinstance(run, dict) or set(run) != {"id", "revision", "covers_main_path", "outcome"}:
            reject()
        ident, rev, scope, outcome = (run[k] for k in ("id", "revision", "covers_main_path", "outcome"))
        if (not isinstance(ident, str) or not RUN_ID.fullmatch(ident) or ident in index
                or not isinstance(rev, str) or not SHA.fullmatch(rev) or type(scope) is not bool
                or not isinstance(outcome, str) or outcome not in ("passed", "failed", "inconclusive")):
            reject()
        index[ident] = (rev.lower(), scope, outcome)
    locator = "normalized input: verification-record"
    if claim is None:
        if not index:
            return absent
        obs = f"{len(index)} historical run records were supplied, with no linked verification claim to assess."
    elif claim not in index:
        obs = "Not verifiable: the claimed historical check does not resolve to a supplied run record."
    elif not revision:
        obs = "Not verifiable: the audited Git revision is unavailable; run coverage cannot be tied to the snapshot."
    else:
        rev, scope, outcome = index[claim]
        if rev != revision.lower():
            obs = "Not supported by the cited run: the run refers to a different Git revision."
        elif not scope:
            obs = "Not supported by the cited run: its supplied scope does not cover the claimed main path."
        elif outcome == "failed":
            obs = "Not supported by the cited run: the supplied result reports failure."
        elif outcome == "inconclusive":
            obs = "Not verifiable: the cited run has an inconclusive outcome."
        else:
            obs = "Supported by the supplied record: the cited run reports a pass on the main path at the recorded Git revision."
    return {"status": "reported", "facts": [fact(obs, locator,
            "Records are caller-supplied, not independently reproduced. Other runs may exist. A local working tree may differ from its recorded Git revision.")]}


def audit_folder(root: Path, target: dict, supplied: dict, starter: Path | None, deadline: str | None,
                 hide: set[str]) -> dict:
    from process_evidence import collect

    baseline = static_audit(root, target)
    checks = {c["id"]: c for c in baseline["checks"]}
    found = collect(root, {k: v for k, v in supplied.items() if k != "verification-record"}, starter, deadline)
    try:
        found["verification-record"] = verification_record(supplied.get("verification-record"), target.get("revision"))
    except ValueError:
        found["verification-record"] = {"status": "blocked", "facts": [
            {"observation": "Supplied historical verification record does not match the bounded contract."}]}
    limits = LIMITS.copy()
    dims = []
    for family, ids in (("artifact", ARTIFACT), ("process", PROCESS)):
        for dim_id in ids:
            row = dict(id=dim_id, family=family, version=1, status="not_assessed", facts=[], interpretations=[])
            if dim_id in hide:
                row["status"] = "hidden_by_instructor"
            elif family == "process":
                process = found.get(dim_id, {"status": "no_evidence", "facts": []})
                row["status"] = process["status"]
                if process["status"] == "reported":
                    row["facts"] = process["facts"]
                elif process["status"] == "blocked":
                    limits.extend(f"{dim_id}: {reason['observation']}" for reason in process["facts"])
            else:
                alternative = CONFOUNDS.get(dim_id, "A static inventory cannot establish this outcome.")
                rows = [checks[k] for k in ARTIFACT[dim_id] if checks[k]["status"] == "checked"]
                for check in rows:
                    row["facts"].append(fact(check["detail"], "STEM static intake", alternative))
                    row["facts"].extend(
                        fact(e["observation"], e["path"] + (f":{e['line']}" if "line" in e else ""), alternative)
                        for e in check["evidence"]
                    )
                findings_for = {"purpose-scope": {"DOC-001"}, "verification": {"TEST-001"},
                                "documentation-licensing": {"DOC-002"}}
                for issue in baseline["findings"]:
                    if issue["id"] in findings_for.get(dim_id, ()):
                        row["facts"].extend(fact(e["observation"], e["path"], alternative)
                                            for e in issue["evidence"])
                if row["facts"]:
                    row["status"] = "reported"
                elif any(checks[k]["status"] == "blocked" for k in ARTIFACT[dim_id]):
                    row["status"] = "blocked"
            dims.append(row)

    base_target = baseline["target"]
    return {
        "schema_version": 1,
        "skill_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {k: base_target[k] for k in ("name", "kind", "revision", "source", "snapshot", "inventoried_files",
                                               "inventory_capped", "omissions") if k in base_target},
        "model": {"name": None, "version": None},
        "dimensions": dims,
        "limitations": limits,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="frictional", description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subs = parser.add_subparsers(dest="action", required=True)
    audit = subs.add_parser("audit", help="inspect a local directory or a GitHub URL")
    audit.add_argument("target")
    audit.add_argument("--out", type=Path, required=True)
    audit.add_argument("--ref", help="full commit SHA for a GitHub URL")
    audit.add_argument("--history-depth", type=int, default=100, help="GitHub fetch depth, 1–500 (default 100)")
    audit.add_argument("--evidence", type=Path, help="optional bounded normalized JSON, never raw LMS/chat exports")
    audit.add_argument("--profile", type=Path, help="versioned field profile with a hide list")
    audit.add_argument("--starter", type=Path, help="explicit local starter directory")
    audit.add_argument("--deadline", help="ISO 8601 timestamp with time zone for descriptive commit timing")
    val = subs.add_parser("validate", help="validate a Frictional report JSON")
    val.add_argument("report", type=Path)
    ren = subs.add_parser("render", help="render a validated report to a new directory")
    ren.add_argument("report", type=Path)
    ren.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action in {"validate", "render"}:
            report = read_object(args.report)
            validate_report(report)
            if args.action == "render":
                write_reports(report, args.out.expanduser())
                print("Rendered report.md and report.json.")
            else:
                print("Valid Frictional report.")
            return 0
        if args.out.expanduser().exists() or args.out.expanduser().is_symlink():
            raise ValueError("Choose a new output directory; Frictional will not overwrite.")
        if not 1 <= args.history_depth <= 500:
            raise ValueError("--history-depth must be between 1 and 500.")
        if args.deadline:
            try:
                if datetime.fromisoformat(args.deadline.replace("Z", "+00:00")).utcoffset() is None:
                    raise ValueError
            except ValueError:
                raise ValueError("--deadline must be an ISO 8601 timestamp with time zone.") from None
        out = args.out.expanduser().resolve()
        hide = hidden_ids(args.profile)
        supplied = read_object(args.evidence) if args.evidence else {}
        if set(supplied) - set(PROCESS):
            raise ValueError("Evidence JSON contains an unrecognized dimension ID.")
        supplied = {k: v for k, v in supplied.items() if k not in hide}
        starter = args.starter.expanduser() if args.starter and "template-delta" not in hide else None
        if starter is not None:
            if starter.is_symlink() or not starter.is_dir():
                raise ValueError("--starter must point to a normal local directory.")
            starter = starter.resolve(strict=True)
            if within_tree(out, starter):
                raise ValueError("Choose an output directory outside the starter.")
        match = REMOTE.fullmatch(args.target)
        if match:
            repo = match.group(2).removesuffix(".git")
            if not repo or repo in {".", ".."}:
                raise ValueError("Invalid GitHub repository name.")
            if args.ref and not SHA.fullmatch(args.ref):
                raise ValueError("--ref must be a full 40-character Git SHA.")
            source = f"https://github.com/{match.group(1)}/{repo}"
            with tempfile.TemporaryDirectory(prefix="frictional-") as tmp:
                options = {"depth": args.history_depth}
                root, sha = clone_repo(source + ".git", args.ref, Path(tmp), **options)
                report = audit_folder(root, {"name": repo, "kind": "github", "source": source, "revision": sha,
                                             "snapshot": f"Fetched to depth at most {args.history_depth}; not all branches."},
                                      supplied, starter, args.deadline, hide)
        else:
            if "://" in args.target or args.ref:
                raise ValueError("Use a local directory or a canonical GitHub repository URL; --ref is remote-only.")
            root = Path(args.target).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Local target must be a directory.")
            if within_tree(out, root):
                raise ValueError("Choose an output directory outside the reviewed project.")
            from process_evidence import git_head
            try:
                revision = git_head(root).decode("ascii")
            except (OSError, ValueError):
                revision = None
            report = audit_folder(root, {"name": safe_text(root.name), "kind": "folder", "revision": revision,
                                         "snapshot": "Working directory; may differ from Git HEAD."},
                                  supplied, starter, args.deadline, hide)
        write_reports(report, out)
        print("Wrote report.md and report.json.")
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"frictional: {str(exc) if isinstance(exc, ValueError) else 'Cannot access input/output or operation timed out.'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
