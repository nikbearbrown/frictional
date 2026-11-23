"""Read-only STEM review inventory and report CLI. Standard library only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from reporting import validate_report, write_reports

VERSION = "0.1.0"
REMOTE = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_][A-Za-z0-9_.-]*)/?")
SHA = re.compile(r"[0-9a-fA-F]{40}")
SKIP = {".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "__pycache__",
        ".tox", ".next", ".godot", "Library", "pantry", "dist", "build", "coverage", ".cache"}
MAX_FILES = 30000
MAX_ENTRIES = 100000
MAX_READ = 256 * 1024
MANIFESTS = {"pyproject.toml", "setup.py", "requirements.txt", "package.json", "Cargo.toml",
             "go.mod", "Project.toml", "environment.yml", "renv.lock", "pom.xml", "build.gradle",
             "build.gradle.kts", "project.godot", "CMakeLists.txt", "Makefile", "Dockerfile",
             "Gemfile", "DESCRIPTION", "mix.exs", "dune-project", "Package.swift", "composer.json"}
LOCKS = {"uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock", "package-lock.json",
         "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum", "Manifest.toml", "renv.lock",
         "Gemfile.lock", "bun.lock", "bun.lockb", "composer.lock"}
DOCS = ("readme", "install", "usage", "reproducibility", "contributing", "getting-started")
SETUP = re.compile(r"\b(install|setup|getting started|quickstart|prerequisite|build|requirements)\b", re.I)
TEST_COMMAND = re.compile(r"\b(pytest|unittest|npm (?:run )?test|pnpm (?:run )?test|yarn test|cargo test|go test|ctest|gradle(?:w)? test|mvn test|dotnet test|testthat)\b", re.I)


def safe_text(value: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in value)


def local_head(root: Path) -> str | None:
    # Read only the root marker. Do not invoke Git on an untrusted local checkout.
    head = root / ".git" / "HEAD"
    try:
        if (root / ".git").is_symlink() or head.is_symlink() or not head.is_file() or head.stat().st_size > 1024:
            return None
        value = head.read_text(encoding="ascii").strip()
        if SHA.fullmatch(value):
            return value
        if value.startswith("ref: "):
            ref = value[5:]
            if re.fullmatch(r"refs/(?:heads|tags)/[a-zA-Z0-9._/-]+", ref) and ".." not in ref:
                p = root / ".git"
                for part in ref.split("/"):
                    p = p / part
                    if p.is_symlink():
                        return None
                if p.is_file() and p.stat().st_size <= 1024:
                    value = p.read_text(encoding="ascii").strip()
                    if SHA.fullmatch(value):
                        return value
    except (OSError, UnicodeError):
        pass
    return None


def within_tree(path: Path, root: Path) -> bool:
    if path.is_relative_to(root):
        return True
    # Resolved spelling is not sufficient on case-insensitive or Unicode-normalizing volumes.
    for ancestor in path.parents:
        try:
            if ancestor.samefile(root):
                return True
        except FileNotFoundError:
            continue
    return False


def inventory(root: Path):
    files: list[str] = []
    omissions = {"excluded_entries": 0, "links_or_special_files": 0, "unreadable_entries": 0}
    examined, capped = 0, False

    def onerror(_error):
        omissions["unreadable_entries"] += 1

    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        base = Path(directory)
        keep = []
        for name in sorted(dirs):
            examined += 1
            if examined > MAX_ENTRIES:
                capped = True
                break
            p = base / name
            if name in SKIP:
                omissions["excluded_entries"] += 1
            elif p.is_symlink():
                omissions["links_or_special_files"] += 1
            else:
                keep.append(name)
        if capped:
            break
        dirs[:] = keep
        for name in sorted(names):
            examined += 1
            if examined > MAX_ENTRIES or len(files) >= MAX_FILES:
                capped = True
                break
            p = base / name
            # Do not even enumerate likely credential filenames in exported reports.
            if name == ".git" or name.startswith(".env") or name.lower().endswith((".pem", ".key", ".p12", ".pfx")):
                omissions["excluded_entries"] += 1
                continue
            try:
                if stat.S_ISREG(p.lstat().st_mode):
                    files.append(p.relative_to(root).as_posix())
                else:
                    omissions["links_or_special_files"] += 1
            except OSError:
                omissions["unreadable_entries"] += 1
        if capped:
            break
    return sorted(files), omissions, capped


def read_bounded(root: Path, rel: str) -> str:
    # Open relative to directory descriptors so a swapped symlink cannot escape the target.
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        return ""
    parts = rel.split("/")
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_READ:
                return ""
            raw = stream.read(MAX_READ + 1)
        if b"\0" in raw or len(raw) > MAX_READ:
            return ""
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        if fd is not None:
            os.close(fd)


def evidence(path: str, observation: str, line: int | None = None):
    row = {"path": safe_text(path), "observation": observation}
    if line is not None:
        row["line"] = line
    return row


def static_audit(root: Path, target: dict) -> dict:
    files, omissions, capped = inventory(root)
    target = dict(target, inventoried_files=len(files), omissions=omissions,
                  inventory_capped=capped, scan_scope="Bounded filename inventory and selected documentation; not a full source review.")
    checks, findings = [], []

    def check(id, title, status, detail, rows=()):
        checks.append(dict(id=id, title=title, status=status, detail=detail, evidence=list(rows)))

    def finding(id, title, severity, confidence, why, rows, action, acceptance):
        findings.append(dict(id=id, title=title, severity=severity, confidence=confidence,
                             why_it_matters=why, evidence=list(rows), recommended_action=action,
                             acceptance_checks=list(acceptance)))

    readmes = [p for p in files if "/" not in p and Path(p).stem.lower() == "readme"]
    docs = sorted(p for p in files if Path(p).stem.lower() in DOCS and
                  ("/" not in p or p.startswith(("docs/", "doc/"))))[:12]
    manifests = [p for p in files if Path(p).name in MANIFESTS or p.endswith((".csproj", ".sln"))]
    locks = [p for p in files if Path(p).name in LOCKS]
    tests = [p for p in files if re.search(r"(?:^|/)(?:tests?|spec|__tests__)/|(?:^|/)test_[^/]+\.|(?:_test|\.test|\.spec)\.", p)]
    ci = [p for p in files if re.search(r"^\.github/workflows/.*\.ya?ml$|^(?:\.gitlab-ci\.yml|Jenkinsfile|azure-pipelines\.yml|\.circleci/config\.yml)$", p)]
    licenses = [p for p in files if "/" not in p and Path(p).stem.lower() in {"license", "licence", "copying"}]
    science = [p for p in files if re.search(r"(?:^|/)(?:reproduce|experiments?|data|notebooks?|results|benchmarks?|paper)(?:/|\.|$)|\.ipynb$", p, re.I)]
    complete = not capped and not omissions["unreadable_entries"]

    check("inventory", "Scope and inventory", "checked" if complete else "blocked",
          f"{len(files)} regular files inventoried; caps, skips and inaccessible entries are recorded in target metadata.")
    check("documentation", "Purpose, installation and usage", "checked",
          "Looked for a root README and setup/test cues in common documentation; did not validate the instructions.",
          [evidence(p, "Documentation candidate.") for p in docs[:6]])
    if not readmes and complete:
        finding("DOC-001", "No root README under a conventional name", "medium", "high",
                "A first-time reviewer may not know what the project does or how to try it.",
                [evidence(".", "No conventional root README in the scanned inventory.")],
                "Add a root README with purpose, prerequisites, one minimal workflow, and where to find tests.",
                ["A new reviewer can locate setup instructions and the expected result from the repository root."])

    cues = {"setup": [], "test": []}
    for path in docs:
        for n, line in enumerate(read_bounded(root, path).splitlines(), 1):
            # Report line locations only, not target-provided commands or text.
            if len(cues["setup"]) < 5 and SETUP.search(line):
                cues["setup"].append(evidence(path, "Possible setup instructions; inspect before use.", n))
            if len(cues["test"]) < 5 and TEST_COMMAND.search(line):
                cues["test"].append(evidence(path, "Possible test command; inspect before use.", n))
    check("dependencies", "Environments and dependency declaration", "checked",
          f"Found {len(manifests)} conventional manifests and {len(locks)} conventional lockfiles. Did not assess dependency health or installability.",
          [evidence(p, "Manifest candidate.") for p in manifests[:8]] +
          [evidence(p, "Lockfile candidate.") for p in locks[:5]] + cues["setup"])
    check("test_inventory", "Tests and CI configuration", "checked",
          f"Found {len(tests)} test-path candidates and {len(ci)} CI configuration candidates. Naming and presence do not prove coverage or execution.",
          [evidence(p, "Test-path candidate.") for p in tests[:8]] +
          [evidence(p, "CI configuration candidate, not a run receipt.") for p in ci[:6]] + cues["test"])
    if complete and not (tests or ci or cues["test"]) and manifests:
        finding("TEST-001", "Test workflow was not discoverable", "medium", "medium",
                "The inventory could not identify how to verify the project; tests might live in a nonstandard location.",
                [evidence(p, "Manifest found, but no conventional test files, CI files, or test instructions were discovered.") for p in manifests[:3]],
                "Document the test entry point and add an automated check of a core behavior if one does not exist.",
                ["The README identifies the exact test entry point.", "A test checks the expected outcome of a representative project workflow."])
    check("licensing", "License and citation signposts", "checked",
          "Looked for conventional root license/citation files; did not establish rights, compliance or attribution.",
          [evidence(p, "License or citation candidate.") for p in (licenses + [p for p in files if p.lower() == "citation.cff"])[:6]])
    if not licenses and complete:
        finding("DOC-002", "No conventional root license file found", "low", "high",
                "Reuse terms are not obvious from the root file inventory; licensing may be documented elsewhere.",
                [evidence(".", "No root LICENSE, LICENCE, or COPYING filename in the scanned inventory.")],
                "Identify the intended reuse terms and, if appropriate, put them in a root license file.",
                ["A reviewer can locate the project's reuse terms without assuming public source is openly licensed."])
    check("scientific_artifacts", "Data, experiments and result artifacts", "checked",
          f"Found {len(science)} conventionally named artifacts. No methodological or empirical claim verified.",
          [evidence(p, "Potential scientific or reproducibility artifact.") for p in science[:8]])
    for id, title, detail in [
        ("code_review", "Correctness, architecture and maintainability", "An agent or reviewer must trace representative workflows and inspect specific source and tests."),
        ("operational_readiness", "Security, privacy, accessibility and performance", "Requires domain-appropriate review; no exploit execution, secret collection or deployment."),
        ("scientific_validity", "Scientific or engineering claims", "Compare claims with data, methods, units, baselines, uncertainty and results; mark not applicable only with a reason."),
        ("runtime", "Clean build, test execution and result reproduction", "No target commands ran. Use a separately approved isolated environment and record command, environment, revision, exit status, test counts, and artifacts."),
    ]:
        check(id, title, "not_assessed", detail)
    return dict(schema_version="1.0", target=target, checks=checks, findings=findings)


def clone_repo(url: str, revision: str | None, temp: Path, depth: int = 1) -> tuple[Path, str]:
    if type(depth) is not int or not 1 <= depth <= 500:
        raise ValueError("Git fetch depth must be between 1 and 500.")
    if not shutil.which("git"):
        raise ValueError("Git is required for GitHub URL targets.")
    temp_home = temp / "git-home"
    temp_home.mkdir()
    env = {key: val for key, val in os.environ.items() if not key.startswith(("GIT_", "LD_", "DYLD_"))}
    env["GIT_TEMPLATE_DIR"] = str(temp_home)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_LFS_SKIP_SMUDGE="1", GIT_ATTR_NOSYSTEM="1", XDG_CONFIG_HOME=str(temp_home))
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.allow=never",
               "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
               "-c", "credential.helper="]
    # Use the caller's existing GitHub CLI login, if available, for private repositories.
    gh = shutil.which("gh")
    if gh:
        import shlex
        command += ["-c", "credential.helper=!" + shlex.quote(gh) + " auth git-credential"]
        gh_config = os.environ.get("GH_CONFIG_DIR") or str(Path.home() / ".config" / "gh")
        env["GH_CONFIG_DIR"] = gh_config
    root = temp / "checkout"

    def run(args: list[str], limit=120) -> str:
        result = subprocess.run(command + args, env=env, cwd=temp, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=limit, check=False)
        if result.returncode:
            raise ValueError(f"Git {args[0]} failed (exit {result.returncode}); check repo access, GitHub login and revision.")
        return result.stdout.strip()

    if revision:
        root.mkdir()
        run(["init", str(root)])
        run(["-C", str(root), "fetch", "--no-recurse-submodules", "--depth", str(depth), url, revision])
        run(["-C", str(root), "checkout", "--detach", "--no-recurse-submodules", "FETCH_HEAD"])
    else:
        run(["clone", "--template=", "--no-recurse-submodules", "--depth", str(depth), "--", url, str(root)])
    sha = run(["-C", str(root), "rev-parse", "--verify", "HEAD"], 15)
    if not SHA.fullmatch(sha) or (revision and revision.lower() != sha):
        raise ValueError("GitHub checkout did not match the requested revision.")
    return root, sha


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stem-review", description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subs = parser.add_subparsers(dest="action", required=True)
    audit = subs.add_parser("audit", help="inventory a local folder or canonical GitHub repository URL")
    audit.add_argument("target")
    audit.add_argument("--out", type=Path, required=True, help="new report directory outside the target")
    audit.add_argument("--ref", help="40-character Git commit SHA for GitHub URL targets")
    validate = subs.add_parser("validate", help="validate report.json without running anything in the report")
    validate.add_argument("report", type=Path)
    render = subs.add_parser("render", help="render validated JSON into a new Markdown/JSON report pair")
    render.add_argument("report", type=Path)
    render.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action in {"validate", "render"}:
            report = json.loads(args.report.read_text(encoding="utf-8"))
            validate_report(report)
            if args.action == "render":
                out = args.out.resolve()
                if out.exists():
                    raise ValueError("Report directory already exists. Choose a new directory.")
                write_reports(report, out)
                print(f"Reports: {out}")
            else:
                print("Valid STEM review report.")
            return 0
        out = args.out.resolve()
        if out.exists():
            raise ValueError("Report directory already exists. Choose a new directory.")
        match = REMOTE.fullmatch(args.target)
        if match:
            repo = match.group(2).removesuffix(".git")
            if not repo or repo in {".", ".."}:
                raise ValueError("Invalid GitHub repository name.")
            if args.ref and not SHA.fullmatch(args.ref):
                raise ValueError("--ref must be a full 40-character commit SHA.")
            source = f"https://github.com/{match.group(1)}/{repo}"
            with tempfile.TemporaryDirectory(prefix="stem-review-") as tmp:
                root, sha = clone_repo(source + ".git", args.ref, Path(tmp))
                report = static_audit(root, {"name": repo, "kind": "github", "source": source, "revision": sha})
        else:
            if "://" in args.target or args.ref:
                raise ValueError("Use a local directory or https://github.com/OWNER/REPO. --ref is only for GitHub URLs.")
            root = Path(args.target).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Local target must be a directory.")
            if within_tree(out, root):
                raise ValueError("Choose a report output directory outside the reviewed project.")
            report = static_audit(root, {"name": safe_text(root.name), "kind": "folder",
                                         "revision": local_head(root), "snapshot": "Working directory; may differ from Git HEAD."})
        write_reports(report, out)
        print(f"Reports: {out}")
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"stem-review: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
