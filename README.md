# Frictional

A skill and command-line tool for reviewing **what a STEM project accomplishes** and **what the available evidence shows about how it was made**. It produces one evidence record in two forms: `report.md` for a person and `report.json` for a tool. It does not grade, score genuine learning, judge authorship, or make an academic-integrity decision.

What it checks:

- **The artifact:** scope, documentation, tests, reproducibility, licensing (the STEM review baseline).
- **The process record:** commit history (also for a student folder inside a course repository), README, naming and repository hygiene.
- **The course's Frictional files:** `FRICTIONAL.md` against the five rubric rows, `PREDICTIONS.md` (confidence before, outcome after), `VERIFICATION.md` and `CONTRIBUTIONS.md`, and whether log dates line up with commits. Counts and line numbers only, never the student's sentences.
- **The seven GLP friction components** of the preprint: a table showing which have evidence in this project, which need course data such as quizzes, and which have none. No GLP score is computed.
- **Optionally, does it install and run?** `--run` installs and runs the project inside a fresh Linux container (Docker or Podman), never on your computer, and `--run-history N` runs the tests at each of the last N commits.
- **Next steps** with priorities (P0 to P2) and an acceptance check for each, so a student knows what to fix and how to tell it worked. `--mode instructor` adds a separate private `instructor-notes.md`.

A passing run can be turned into a short walkthrough film with the `frictional` skill in the [brutalist.art](https://github.com/nikbearbrown/brutalist.art) toolkit.

## Start here

Python 3.10+ on macOS or Linux is required (on Windows, use WSL: the file checks rely on POSIX no-follow file access). Git is required for an explicitly requested GitHub fetch and certain history checks. There are no runtime Python packages to install.

```sh
./frictional audit /path/to/project --out /tmp/frictional-review
./frictional audit /path/to/project --out /tmp/frictional-run --run --run-history 10
./frictional audit /path/to/student-folder --out /tmp/frictional-instructor --mode instructor --run
./frictional audit https://github.com/OWNER/REPO --out /tmp/frictional-github --history-depth 100
./frictional run /path/to/project --out /tmp/frictional-serve --spec spec.json --serve 900
./frictional validate /tmp/frictional-review/report.json
./frictional render /tmp/frictional-review/report.json --out /tmp/frictional-render
```

`--run` needs Docker or Podman running (on a Mac, Docker Desktop, OrbStack, or `brew install colima docker && colima start`). Without `--run-spec`, Frictional guesses only install and test commands from `requirements.txt`, `pyproject.toml` or `package.json`, and labels them as guesses. To test the commands your README actually gives, write a spec. Save it as `frictional-run.json` at the top of your project and `--run` uses it automatically (or pass any file with `--run-spec`):

```json
{
  "image": "python:3.12-slim",
  "setup": [{"cmd": "python -m pip install -r requirements.txt", "source": "README.md:12"}],
  "run":   [{"cmd": "python wc.py sample.txt", "source": "README.md:18"}],
  "test":  [{"cmd": "python -m unittest discover -v", "source": "README.md:24"}],
  "serve": {"cmd": "python app.py --host 0.0.0.0", "port": 8000, "path": "/", "source": "README.md:30"}
}
```

Use a new directory outside the reviewed folder, with an existing parent. `--ref` pins a GitHub checkout to a full commit SHA. GitHub retrieval is time-bounded and limited to the opted-in repository, not byte-capped; for a large repository use a local checkout. Pass an explicit, bounded synthetic or authorized normalized file with `--evidence`, a comparison folder with `--starter`, and a timezone-qualified ISO timestamp with `--deadline`. The [general profile](profiles/general.json) hides nothing; an instructor can opt out of dimensions explicitly.

For a substantive audit, give a trusted agent [the skill](skills/frictional/SKILL.md) and the target. The CLI is static intake and deterministic evidence collection, not an independent scientific review. It will find conventional tests and install instructions but won't test whether they work. The agent can add source-linked interpretation with its actual model name and version, then render the validated record again. Never execute target code, probe deployment, change the project or post findings automatically.

## Read the evidence in two streams

- **Artifact:** the existing STEM rubric and CLI are retained. Scope, reproducibility, correctness, tests, architecture, documentation, licensing and scientific validity still matter. `./stem-review` remains available when the full original STEM-format output is wanted. Frictional turns those intake observations into neutral statements; major lens judgments remain unassessed without substantive review.
- **Process:** describe visible commits, supplied push events, documented checks, repository conventions, starter comparison and supplied course observations separately. Commit dates are editable, can omit offline work, and never establish hours worked. A push is not a commit. A sparse history or missing record never lowers a grade.
- **Evidence status:** every default dimension is visible. `no_evidence`, `blocked`, `not_assessed`, and `hidden_by_instructor` are different states, not negative results. A reported observation can have alternative explanations. A caller-supplied receipt is not an independent rerun.

See the [measurement rules](MEASUREMENT.md), [dimension cards](skills/frictional/references/dimensions.md) and [JSON schema](frictional.schema.json).

## Privacy

Frictional sends no data to Irreducibly Human and has no telemetry. A GitHub URL causes an explicit fetch from GitHub; a local audit does not need network access. With `--run`, the project's code runs only inside a local container: the folder is copied in (never mounted), the container gets limited memory, processes and privileges, and network access only while installing (or while serving an app you asked to serve). Package managers download dependencies from their usual registries during setup. Run logs are kept in `OUT/run/` with likely secrets masked; read them before sharing. A separate agent or hosting platform may process data according to its own rules. Do not supply raw LMS records, transcripts, real names, grades, or secrets to this prototype. Evidence JSON is deliberately narrow and caller-supplied. Do not develop or test with student work. Inspect and redact reports before sharing. Never run install or test commands from an untrusted repository on the host. This version accepts normalized external receipts only, not commands or raw transcripts.

## Provenance and development

Based on the user’s STEM Project Review implementation at `ac166a607d70b44e354cc7c142b9d78357887e71`, with remote fetch depth parameterized while keeping the original command's default. See [STEM baseline](STEM-BASELINE.md). No third-party project material, student records, or real benchmark data are included. Review rights to the private baseline before any external release. Synthetic regression tests test the tool’s behavior, not student learning or validity in a course.

```sh
python3 -B -m unittest discover -v
```
