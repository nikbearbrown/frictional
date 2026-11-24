"""Read-only Frictional audit: static STEM baseline and separately sourced process evidence."""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    sys.exit("frictional: needs Python 3.10 or newer; this is Python %d.%d.%d.\n"
             "Install a newer Python from https://www.python.org/downloads/ "
             "(on a Mac, also: brew install python), then run the command again." % sys.version_info[:3])

import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import tempfile

from frictional_reporting import validate_report, write_instructor_notes, write_reports
from stem_project_review import REMOTE, SHA, clone_repo, safe_text, static_audit, within_tree

VERSION = "0.2.0"
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
    "commit-history", "push-timeline", "install-test", "run-check", "test-history", "serve-check",
    "readme-completeness", "naming-conventions", "repo-hygiene", "template-delta", "contribution-distribution",
    "process-log", "log-rubric-rows", "log-commit-consistency", "prediction-log", "verification-notes",
    "contributions-log", "verification-record", "error-trajectory", "temporal-engagement", "transfer",
    "calibration", "retrieval", "scaffolding",
)
SUPPLIABLE = {"push-timeline", "install-test", "error-trajectory", "temporal-engagement", "transfer",
              "calibration", "retrieval", "scaffolding", "verification-record"}
RUN_DIMS = ("install-test", "run-check", "test-history", "serve-check")
# The seven friction components of the GLP preprint ("Measuring the Struggle") and the
# dimensions that can carry evidence for each. "course" components need data a code
# repository cannot hold (quizzes, time on task, hint experiments).
GLP = (
    ("Y1", "Temporal engagement pattern", ("temporal-engagement",), "course",
     "Needs time on task by item difficulty from the course, for example Canvas. Commit times are not time on task."),
    ("Y2", "Error trajectory coherence", ("test-history", "error-trajectory", "verification-notes", "log-rubric-rows"), "repo",
     "From tests run at each commit (--run-history), failed checks in VERIFICATION.md and the friction-and-response rows of FRICTIONAL.md. Whether errors follow the concept's structure needs a reader."),
    ("Y3", "Cross-context transfer", ("transfer",), "course",
     "Needs near and far transfer items given by the course."),
    ("Y4", "Uncertainty calibration", ("prediction-log", "calibration"), "repo",
     "From the student's own numeric predictions in PREDICTIONS.md, or supplied quiz confidence data."),
    ("Y5", "Social knowledge texture", ("log-rubric-rows", "process-log", "log-commit-consistency"), "repo",
     "FRICTIONAL.md shows where texture can appear. Coding personal encounter, below-surface engagement, productive uncertainty and change over time is a reader's interpretation, recorded separately."),
    ("Y6", "Retrieval strength decay", ("retrieval",), "course",
     "Needs spaced retrieval quizzes from the course."),
    ("Y7", "Scaffolding response curve", ("scaffolding",), "course",
     "Needs hint-level experiments from the course."),
)
PRIORITY = {"P0": 0, "P1": 1, "P2": 2}
NO_RUN = "No target code, tests, or install instructions were executed. An external receipt is supplied evidence, not a reproduced result."
RAN = ("Project commands ran only inside an isolated Linux container, never on this computer (see run/receipt.json). "
       "A clean container is not the author's machine; a failure there can come from undocumented prerequisites.")
LIMITS = [
    "This is a bounded static intake, not a full source review or validation of scientific claims.",
    NO_RUN,
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


def glp_table(dims: list[dict]) -> list[dict]:
    status = {d["id"]: d["status"] for d in dims}
    rows = []
    for ident, name, sources, kind, note in GLP:
        seen = [{"id": s, "status": status.get(s, "not_assessed")} for s in sources]
        if any(x["status"] == "reported" for x in seen):
            state = "evidence_recorded"
        elif kind == "course":
            state = "needs_course_data"
        else:
            state = "no_evidence"
        rows.append({"id": ident, "name": name, "status": state, "sources": seen, "note": note})
    return rows


def rule_steps(dims: list[dict], receipt: dict | None) -> list[dict]:
    """Next steps that follow from dimension states and the run receipt (fixed wording)."""
    status = {d["id"]: d["status"] for d in dims}
    steps = []
    if status.get("process-log") == "no_evidence":
        steps.append({"priority": "P1", "dimension": "process-log", "locator": "FRICTIONAL.md",
                      "change": "Add FRICTIONAL.md at the top of the folder you hand in, using the course template, with short dated entries.",
                      "check": "Rerun the audit: process-log and log-rubric-rows show Reported."})
    if status.get("commit-history") == "no_evidence":
        steps.append({"priority": "P2", "dimension": "commit-history", "locator": "git",
                      "change": "Commit your work to Git as you go, so the history shows how the project developed.",
                      "check": "Rerun the audit: commit-history shows Reported."})
    if receipt:
        outcome = (receipt.get("overall") or receipt.get("phases") or {})
        failing = [c for c in receipt.get("commands", []) if c.get("exit_code") not in (0, None) or c.get("timed_out")]
        def first(phase):
            return next((c for c in failing if c.get("phase") == phase), None)
        if outcome.get("setup") == "failed":
            c = first("setup") or {}
            steps.append({"priority": "P0", "dimension": "install-test", "locator": c.get("log", "run/receipt.json"),
                          "change": f"The project did not install on a clean Linux machine. The setup command from {c.get('source', 'the run spec')} failed; "
                                    "fix that instruction or list the missing prerequisite in the README.",
                          "check": "Rerun with --run: install-test reports the setup phase passed."})
        if outcome.get("run") == "failed":
            c = first("run") or {}
            steps.append({"priority": "P0", "dimension": "run-check", "locator": c.get("log", "run/receipt.json"),
                          "change": f"The documented run command ({c.get('source', 'run spec')}) failed after a clean install. Make the documented command work as written.",
                          "check": "Rerun with --run: run-check reports the run phase passed."})
        if outcome.get("test") == "failed":
            c = first("test") or {}
            steps.append({"priority": "P1", "dimension": "run-check", "locator": c.get("log", "run/receipt.json"),
                          "change": "Tests failed in a clean Linux container. Read the log, fix the failure or the test, and say in FRICTIONAL.md what you found.",
                          "check": "Rerun with --run: the test phase passes."})
        if outcome.get("serve") == "failed":
            steps.append({"priority": "P1", "dimension": "serve-check", "locator": "run/receipt.json",
                          "change": "The app did not answer on its documented port. Check the serve command, host (use 0.0.0.0 inside a container) and port.",
                          "check": "Rerun with --run: serve-check reports that the app answered."})
    return steps


def audit_folder(root: Path, target: dict, supplied: dict, starter: Path | None, deadline: str | None,
                 hide: set[str], run: dict | None = None, mode: str = "student", out: Path | None = None,
                 notes_out: list | None = None) -> dict:
    """Collect evidence. run: None, or {"spec": dict|None, "history": int} to run inside a container.
    notes_out, if given, receives instructor-only notes (never part of the report)."""
    from process_evidence import collect

    baseline = static_audit(root, target)
    checks = {c["id"]: c for c in baseline["checks"]}
    context: dict = {}
    found = collect(root, {k: v for k, v in supplied.items() if k != "verification-record"}, starter, deadline,
                    context=context)
    receipt = None
    run_summary = None
    if run is not None and not RUN_DIMS[0] in hide:
        receipt, run_found, run_summary = container_step(root, run, out, context, target.get("revision"))
        found.update(run_found)
    try:
        found["verification-record"] = verification_record(supplied.get("verification-record"), target.get("revision"))
    except ValueError:
        found["verification-record"] = {"status": "blocked", "facts": [
            {"observation": "Supplied historical verification record does not match the bounded contract."}]}
    limits = LIMITS.copy()
    if receipt is not None:
        limits[1] = RAN
    dims = []
    steps, notes = [], []
    for family, ids in (("artifact", ARTIFACT), ("process", PROCESS)):
        for dim_id in ids:
            row = dict(id=dim_id, family=family, version=1, status="not_assessed", facts=[], interpretations=[])
            if dim_id in hide:
                row["status"] = "hidden_by_instructor"
            elif family == "process":
                process = found.get(dim_id, {"status": "no_evidence", "facts": []})
                steps.extend(process.get("next_steps", []))
                notes.extend(process.get("notes", []))
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
    hidden_steps = {d["id"] for d in dims if d["status"] == "hidden_by_instructor"}
    steps = [x for x in steps + rule_steps(dims, receipt) if x["dimension"] not in hidden_steps]
    steps.sort(key=lambda x: (PRIORITY[x["priority"]], x["dimension"]))
    report = {
        "schema_version": 2,
        "skill_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "target": {k: base_target[k] for k in ("name", "kind", "revision", "source", "snapshot", "inventoried_files",
                                               "inventory_capped", "omissions") if k in base_target},
        "model": {"name": None, "version": None},
        "dimensions": dims,
        "glp": glp_table(dims),
        "next_steps": steps,
        "run": run_summary,
        "limitations": limits,
    }
    if context.get("scope"):
        report["target"]["folder_in_repository"] = context["scope"]
    if notes_out is not None:
        notes_out.extend(notes)
    return report


def run_outcomes(receipt: dict) -> dict:
    """Phase outcomes for the report; history becomes a count, since one failing old commit is not a failure."""
    outcomes = dict(receipt.get("overall") or receipt.get("phases") or {})
    rows = receipt.get("history") or []
    if rows:
        passed = sum(r.get("outcome") == "passed" for r in rows)
        outcomes["history"] = f"{passed} of {len(rows)} commits passed (oldest first: " + \
            ", ".join("pass" if r.get("outcome") == "passed" else "fail" for r in rows) + ")"
    elif outcomes.get("history") == "not_run":
        outcomes.pop("history")
    return outcomes


def container_step(root: Path, run: dict, out: Path | None, context: dict, revision: str | None):
    """Run the project inside a Linux container. Returns (receipt, dimension results, summary)."""
    import container_run as cr
    from process_evidence import blocked

    def stop(message):
        return None, {d: blocked("run", message) for d in RUN_DIMS}, None

    if out is None:
        return stop("No output directory for run records; nothing was run.")
    runtime = cr.find_runtime()
    if runtime is None:
        return stop("No working Docker or Podman was found, so nothing was run. Install Docker (or Podman) and start it, then rerun with --run.")
    spec, spec_source = run.get("spec"), "supplied run spec"
    if spec is None and "frictional-run.json" in context.get("files", {}):
        try:
            spec, spec_source = cr.load_spec(root / "frictional-run.json"), "the project's own frictional-run.json"
        except (OSError, ValueError) as exc:
            return stop(f"The project's frictional-run.json is not a valid run spec ({exc}); nothing was run.")
    if spec is None:
        spec, spec_source = cr.default_spec(root, context.get("files", {})), "default from project manifests"
    if spec is None:
        return stop("No run spec was given and no frictional-run.json, requirements.txt, pyproject.toml or package.json was found at the top of the folder; nothing was run.")
    history = []
    want = run.get("history", 0)
    rows = context.get("rows") or []
    if want and rows and spec.get("test"):
        from gitcli import archive_folder, find_enclosing
        enclosing = find_enclosing(root)
        repo, sub = enclosing if enclosing else (root, "")
        work = Path(tempfile.mkdtemp(prefix="frictional-history-"))
        run["_tmp"] = work
        for n, row in enumerate(reversed(rows[:want])):
            try:
                tar = archive_folder(repo, row[0], sub, work / f"{n:03d}-{row[0][:12]}.tar")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
            history.append({"commit": row[0], "tar": tar})
    try:
        receipt = cr.run_project(root, spec, out, runtime=runtime, history=history or None, revision=revision,
                                 serve=bool(spec.get("serve")))
    finally:
        tmp = run.pop("_tmp", None)
        if tmp is not None:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    summary = {
        "runtime": f"{runtime['name']} {runtime['version']}",
        "image": spec.get("image"),
        "outcomes": run_outcomes(receipt),
        "receipt": "run/receipt.json",
        "spec_source": spec_source,
    }
    return receipt, cr.run_results(receipt), summary


PROBE_COMMAND = True


def main(argv=None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)
    if raw and raw[0] == "probe":
        from probes import main as probe_main
        return probe_main(raw[1:])
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
    audit.add_argument("--mode", choices=("student", "instructor"), default="student",
                       help="student (default): report with next steps. instructor: also writes instructor-notes.md, kept separate from the report")
    audit.add_argument("--run", action="store_true",
                       help="install and run the project inside a fresh Linux container (Docker or Podman); never on this computer")
    audit.add_argument("--run-spec", type=Path, help="run spec JSON: image, setup/run/test/serve commands with their README sources")
    audit.add_argument("--run-history", type=int, default=0, metavar="N",
                       help="with --run: also run the tests at each of the last N commits (0-50), oldest first")
    runp = subs.add_parser("run", help="only run the project in a container (for example to serve it for a film); writes OUT/run/")
    runp.add_argument("target")
    runp.add_argument("--out", type=Path, required=True)
    runp.add_argument("--spec", type=Path, help="run spec JSON; default: guessed from manifests")
    runp.add_argument("--serve", type=int, default=0, metavar="SECONDS",
                      help="start the spec's serve command and keep the app up this long (prints its local URL)")
    subs.add_parser("probe", add_help=False,
                    help="in-class probes from a project's own code: make, references, freeze, receipts "
                         "(see MEASUREMENT.md, Probe receipts)")
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
        if args.action == "run":
            return run_only(args)
        if not 0 <= args.run_history <= 50:
            raise ValueError("--run-history must be between 0 and 50.")
        if (args.run_spec or args.run_history) and not args.run:
            raise ValueError("--run-spec and --run-history need --run.")
        run = None
        notes: list = []
        if args.run:
            import container_run
            run = {"spec": container_run.load_spec(args.run_spec.expanduser()) if args.run_spec else None,
                   "history": args.run_history}
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
        if set(supplied) - SUPPLIABLE:
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
                if run is not None:
                    out.mkdir(mode=0o700)
                report = audit_folder(root, {"name": repo, "kind": "github", "source": source, "revision": sha,
                                             "snapshot": f"Fetched to depth at most {args.history_depth}; not all branches."},
                                      supplied, starter, args.deadline, hide, run=run, mode=args.mode, out=out, notes_out=notes)
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
            if run is not None:
                out.mkdir(mode=0o700)
            report = audit_folder(root, {"name": safe_text(root.name), "kind": "folder", "revision": revision,
                                         "snapshot": "Working directory; may differ from Git HEAD."},
                                  supplied, starter, args.deadline, hide, run=run, mode=args.mode, out=out, notes_out=notes)
        write_reports(report, out)
        written = "report.md and report.json"
        if args.mode == "instructor":
            write_instructor_notes(report, notes, out)
            written += ", plus instructor-notes.md (keep it separate from what you share with the student)"
        print(f"Wrote {written}.")
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"frictional: {str(exc) if isinstance(exc, ValueError) else 'Cannot access input/output or operation timed out.'}", file=sys.stderr)
        return 2


def run_only(args) -> int:
    """`frictional run`: container run without the audit, e.g. to serve the app for a film capture."""
    import container_run as cr
    from process_evidence import inventory
    from gitcli import find_enclosing  # noqa: F401  (keeps the same import surface as audit)

    if REMOTE.fullmatch(args.target) or "://" in args.target:
        raise ValueError("frictional run takes a local folder.")
    root = Path(args.target).expanduser().resolve(strict=True)
    out = args.out.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Local target must be a directory.")
    if within_tree(out, root):
        raise ValueError("Choose an output directory outside the reviewed project.")
    runtime = cr.find_runtime()
    if runtime is None:
        raise ValueError("No working Docker or Podman was found. Install Docker (or Podman), start it, and try again.")
    if args.spec:
        spec = cr.load_spec(args.spec.expanduser())
    elif (root / "frictional-run.json").is_file() and not (root / "frictional-run.json").is_symlink():
        spec = cr.load_spec(root / "frictional-run.json")
    else:
        spec = cr.default_spec(root, inventory(root)[0])
    if spec is None:
        raise ValueError("No run spec given and no manifest found to guess one from; pass --spec.")
    if args.serve and not spec.get("serve"):
        raise ValueError("--serve needs a spec with a serve command.")
    out.mkdir(mode=0o700)
    from process_evidence import git_head
    try:
        revision = git_head(root).decode("ascii")
    except (OSError, ValueError):
        revision = None
    receipt = cr.run_project(root, spec, out, runtime=runtime, serve=bool(args.serve),
                             serve_seconds=args.serve, revision=revision)
    print(f"Run finished: {json.dumps(run_outcomes(receipt))}. Records in {out / 'run'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
