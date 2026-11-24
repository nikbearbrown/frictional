# Frictional

## Executive summary

**What this is.** Frictional is a free command-line tool, with a matching skill for coding agents. It reads a software or research project folder and writes a report on two questions:

- What does the project contain?
- What does the record show about how it was made?

It was built for courses where students keep a Frictional process log (`FRICTIONAL.md`), but it works on any project.

**Why you would use it.** An instructor runs it to gather the same evidence from every project handed in. A student runs the same audit first, to see what a reader can and cannot verify from the folder and fix it before the deadline. Every report ends with next steps, most urgent first. Each step says what to change and how to check that the change worked.

**What it does not do.**

- It does not grade, score or rank anything.
- It is not a plagiarism or AI-use detector, and it makes no academic-integrity judgment.
- "No evidence" means the tool found no record. It does not mean the work never happened.
- It never runs a project's code on your computer. If you add `--run`, the code runs only inside a temporary Linux container.

## What Frictional does, step by step

To audit a folder, run `./frictional audit FOLDER --out REPORT_FOLDER`. Frictional then works through eight steps.

1. **Reads the folder without changing it.**
   - It reads up to a fixed number of files and skips symbolic links.
   - It skips files whose names look like they hold credentials, and it never reads their contents.
   - Nothing in the folder is executed.
   - It contacts GitHub only when you give it a GitHub URL instead of a folder.
2. **Describes the project itself.** It uses the ten artifact checks from the original STEM project review:
   - purpose and scope;
   - the user's path through the project;
   - scientific validity;
   - data and evaluation;
   - reproducibility;
   - correctness;
   - architecture;
   - tests;
   - safety and security;
   - documentation and licensing.

   A quick scan can see that a test file exists, but not whether the tests are good. So a check that needs someone to read the code is marked "Not assessed" instead of guessed.
3. **Reads how the project was made.**
   - **Commit history.** If the project is one student's folder inside a shared course repository, Frictional reads only the commits that touched that folder.
   - **How commits are spread across author names.** This is not a measure of who did the work.
   - **Which README sections exist:** purpose, install, usage, examples, tests, license and AI use.
   - **File naming and repository hygiene.** For example, it counts very large files and skipped cache folders, and notes whether there is a `.gitignore`.
   - **Commit timing against a deadline,** if you give one with `--deadline`.
   - **What changed from the course starter,** if you give one with `--starter`.
4. **Reads the course's Frictional files.**
   - **`FRICTIONAL.md`.** It checks whether each of the five rubric rows has entries and how many: attempts; friction and response; human and AI contributions; learning and uncertainty; traceable process. It also checks whether the log's dates line up with the commit dates.
   - **`PREDICTIONS.md`.** It counts how many predictions state a confidence before the run and an outcome after it. For predictions with a numeric confidence and a yes or no outcome, it also computes how well confidence matched what held: the mean squared error, and confidence minus the share that held.
   - **`VERIFICATION.md` and `CONTRIBUTIONS.md`.** It checks whether they are filled in.

   The report gives counts, dates and line numbers. It never copies a student's sentences. Whether an entry is specific or vague is the instructor's judgment, not the tool's.
5. **Maps the evidence onto the seven GLP friction components.** These come from the Genuine Learning Probability preprint; the [table below](#the-seven-glp-components) lists them. For each component the report says one of three things:
   - evidence is recorded in this project;
   - the component needs course data that a code folder cannot hold, such as quizzes;
   - there is no evidence.

   It never computes a GLP score.
6. **Installs and runs the project, if you ask (`--run`).** This step is optional.
   - Frictional copies the folder into a fresh Linux container (Docker or Podman).
   - It runs the project's own install, run and test commands there. If the run spec serves a web app, it also checks that the app answers.
   - With `--run-history N`, it also reruns the tests at each of the last N commits, oldest first. This shows when tests started passing or failing.
   - The point is the clean-machine test: "it works on my machine" is not the test; a clean Linux machine following the README is. A failure is reported as "did not install or run from its instructions on a clean Linux machine", with the failing command and the line of the log that names the cause. That line is the last error line in the log; for a Python traceback it is the exception line that ends the traceback, and for a pytest collection error it is pytest's `E` line rather than the bare summary. It is not a judgment of the code or the person.
   - Without a run spec, Frictional guesses the install and test commands, and says so. If a guess fails, the report says that the guess failed and that the README's own commands were not tested. It never blames the student for Frictional's own guess.
   - If the hand-in has no code at all (only documents, media or a link), there is nothing to run. The report says so and gives no step about run commands.
7. **Writes next steps,** most urgent first:
   - **P0:** the project's own instructions do not install or run it;
   - **P1:** core evidence is missing or a check failed, such as no README, no `FRICTIONAL.md`, failing tests, or an app that doesn't answer;
   - **P2:** clarity and traceability, such as README sections without setup commands, no tests, no licence, no Git history, or a `.git` folder whose history cannot be read.

   An install section counts only when a command sits under its heading (a code block, a `$` prompt, or an inline command such as `` `pip install ...` ``). A "Getting started" heading with prose only gets the step to add install commands.

   Each step names the change and the check that shows it worked.
8. **Writes the report.**
   - `report.md` is for a person and `report.json` is for tools; both hold the same evidence record.
   - With `--mode instructor`, Frictional also writes `instructor-notes.md`, kept separate from the student's report.
   - With `--run`, the run's receipt and full transcript go in `run/`.

## What's in a report

`report.md` has these sections, in this order:

| Section | What it holds |
|---|---|
| Next steps | P0 to P2 changes, each with how to check it worked. If there are none, the report says so and adds that this is not a verdict on the work. |
| Container run | Whether `--run` was used, and how setup, run, tests and serve came out. With `--run-history`, also how many commits passed. |
| Frictional components (GLP) | The seven-row table described below. |
| Dimension status | Every check, artifact and process, with its status. |
| Observed facts | For each check that found something: the fact, where it came from (a file, line or commit), and the other explanations that could account for it. |
| Model-assisted interpretations | Empty unless an agent added interpretations. They are labelled with the model that wrote them and kept apart from the facts. |
| Limitations | What this kind of audit cannot establish. |

Every check gets one of these statuses. They are different states, and none of them is a negative mark:

| Status | Meaning |
|---|---|
| Reported | The audit recorded something. The status says nothing about whether it is good. |
| No evidence | The tool looked and found no record. That does not mean the work never happened. |
| Blocked | Something stopped the check, such as access or file format. |
| Not applicable | The check does not apply to this project. |
| Not assessed | This audit did not check it. Most often the check needs a person or an agent to read the code. |
| Hidden by instructor | The course profile turned this check off. |

**Read the evidence with care:**

- Commit dates can be edited and can miss offline work, and they never show hours worked. A push is not a commit.
- A sparse history or a missing record never lowers a grade.
- Each fact comes with the other explanations that could account for it.
- A receipt someone else supplies is not an independent rerun.

## The seven GLP components

Evidence for a component is not a measurement of it, and no score is computed.

| Component | Where Frictional looks | In a project folder? |
|---|---|---|
| Y1 Temporal engagement pattern | Time on task by item difficulty, from the course, for example Canvas. Commit times are not time on task. | No: needs course data |
| Y2 Error trajectory coherence | Tests rerun at each commit (`--run-history`), failed checks in `VERIFICATION.md`, and the friction-and-response rows of `FRICTIONAL.md`. Whether the errors follow the concept's structure needs a reader. | Yes |
| Y3 Cross-context transfer | Near and far transfer tasks given by the course, or an [in-class probe](#in-class-probes). | No: needs course data |
| Y4 Uncertainty calibration | The student's own numeric predictions in `PREDICTIONS.md`, or supplied quiz confidence data. | Yes |
| Y5 Social knowledge texture | `FRICTIONAL.md` and how its dates match the commits. These show where texture can appear; coding the texture itself is a reader's interpretation. | Partly |
| Y6 Retrieval strength decay | Spaced retrieval quizzes from the course. | No: needs course data |
| Y7 Scaffolding response curve | Hint-level experiments from the course, or an [in-class probe](#in-class-probes). | No: needs course data |

## Start here

**What you need:**

- Python 3.10 or newer, on macOS or Linux. On Windows, use WSL: the file checks rely on POSIX file access.
- Git, for history checks and for auditing from a GitHub URL.
- No Python packages to install.

Try it on the made-up sample project first, to check your setup. Then run it on your own folder, and write each report to a new folder outside the project:

```sh
./frictional audit examples/word-counter --out ~/frictional-example
./frictional audit /path/to/project --out ~/frictional-report-1
```

Then open `report.md` in the report folder and start with **Next steps**. Frictional never overwrites a report, so use a new `--out` folder each time.

More ways to run it:

```sh
./frictional audit /path/to/project --out ~/frictional-run --run --run-history 10
./frictional audit /path/to/student-folder --out ~/frictional-instructor --mode instructor --run
./frictional audit https://github.com/OWNER/REPO --out ~/frictional-github --history-depth 100
./frictional run /path/to/project --out ~/frictional-serve --spec spec.json --serve 900
./frictional validate ~/frictional-report-1/report.json
./frictional render ~/frictional-report-1/report.json --out ~/frictional-render
```

- `run` only runs the project in a container, without an audit. With `--serve SECONDS` it keeps a web app up for that long and prints its local address.
- `validate` checks a `report.json` against the [schema](frictional.schema.json).
- `render` writes the report again from a validated `report.json`, into a new folder.
- `./stem-review` gives the full original STEM-review output.

**Options for `audit`:**

- `--ref`: pins a GitHub checkout to a full commit SHA. GitHub retrieval is time-bounded. For a large repository, use a local checkout.
- `--starter DIR`: compares the project with a course starter.
- `--deadline`: takes an ISO timestamp with a time zone, for commit timing.
- `--evidence FILE`: takes a bounded, normalized JSON file of course data, such as quiz confidence or probe receipts. It never takes raw LMS exports.
- `--profile FILE`: hides checks that don't apply. The [general profile](profiles/general.json) hides nothing.

## Run the project in a container

`--run` needs Docker or Podman running. On a Mac, Docker Desktop, OrbStack, or `brew install colima docker && colima start` all work.

What happens:

- The project folder is copied into a fresh Linux container. It is never mounted, so the code cannot touch your files.
- The container gets limited memory, CPU and processes, and no extra privileges.
- The network is on while dependencies install, then off, unless you asked it to serve an app. A served app is reachable only from your own computer (`127.0.0.1`).
- Frictional removes the container afterwards.
- It keeps `run/receipt.json`, with each command, its exit code and time, and the outcome of each phase. It also keeps `run/transcript.jsonl` and per-command logs in `run/logs/`, the full output with likely secrets masked.

Without a spec, Frictional guesses only install and test commands from `requirements.txt`, `pyproject.toml` or `package.json`, and labels them as guesses. It looks at the top of the folder. If the top holds no code and exactly one subfolder holds a manifest, it looks in that subfolder instead. To test the commands your README actually gives, write a spec. Each command carries the README line it came from. Save the spec as `frictional-run.json` at the top of the project and `--run` uses it automatically, or pass any file with `--run-spec`:

```json
{
  "image": "python:3.12-slim",
  "setup": [{"cmd": "python -m pip install -r requirements.txt", "source": "README.md:12"}],
  "run":   [{"cmd": "python wc.py sample.txt", "source": "README.md:18"}],
  "test":  [{"cmd": "python -m unittest discover -v", "source": "README.md:24"}],
  "serve": {"cmd": "python app.py --host 0.0.0.0", "port": 8000, "path": "/", "source": "README.md:30"}
}
```

The sample in [`examples/word-counter`](examples/word-counter) has a working `frictional-run.json`.

## In-class probes

The GLP components a repository cannot hold (Y1 timing, Y3 transfer, Y4 calibration, Y6 retrieval, Y7 scaffolding) can come from a short probe built from the project's own code. `probe make`:

- picks 3 to 5 Python functions that differ from the course starter;
- writes a predict-the-output item for each;
- writes one injected-bug item with a hint ladder;
- writes frames for a near and a far transfer task.

It never changes the project, and every answer key comes from running the code in the container (`--run`).

The instructor rates difficulty, writes the transfer tasks, freezes the probe and gives it in class without AI. They then record one receipt per student: item IDs, confidence, timing, hint level and correctness. A receipt never holds answers or names. `probe receipts` turns the receipts into an `--evidence` file.

```sh
./frictional probe make examples/word-counter --starter examples/word-counter-starter --out ~/probe-demo --run --deadline 2027-02-14T23:59:00-05:00
./frictional probe references ~/probe-demo --target examples/word-counter
./frictional probe freeze ~/probe-demo
./frictional probe receipts --spec ~/probe-demo/probes/probe-spec.json receipt-A.json --out evidence.json
./frictional audit examples/word-counter --out ~/probe-audit --evidence evidence.json
```

Probe files are education records: keep them private and never commit them. A probe is for every student in a section, announced in advance, for completion credit only; it is never an integrity check. See [Probe receipts](MEASUREMENT.md#probe-receipts) for what the numbers can and cannot say.

## Use it with a coding agent

The CLI reads and counts. It does not read the code the way a reviewer would. For a fuller review, give a coding agent [the skill](skills/frictional/SKILL.md) and the project. The agent adds interpretations, each linked to its source and labelled with the agent's model name and version, and then renders the record again. The skill's rules still apply to the agent:

- It never runs the project's code on your computer; only the container may.
- It never changes the project.
- It never posts findings anywhere on its own.

See the [measurement rules](MEASUREMENT.md) and the [check cards](skills/frictional/references/dimensions.md) for what each check can and cannot show.

## Make a short film of a working project

If a `--run` passes, the `frictional` skill in the free [brutalist.art](https://github.com/nikbearbrown/brutalist.art) toolkit can turn it into a short narrated walkthrough. The film shows the clean install, the app working, and the code behind what is on screen. It films only what actually ran, uses free tools, and uses the student-mode report only. This step is optional.

## Privacy

- Frictional has no telemetry and sends no data to Irreducibly Human.
- A local audit needs no network. A GitHub URL causes one explicit fetch from GitHub.
- With `--run`, package managers download dependencies from their usual registries during setup.
- Run logs in `run/` have likely secrets masked, but read them before sharing.
- A separate agent or hosting platform may process data under its own rules.
- Do not give this prototype raw LMS records, transcripts, real names, grades or secrets.
- Never run install or test commands from an untrusted project on your own computer. Use `--run`.
- Do not develop or test the tool with student work.
- Inspect and redact reports before sharing them.

## Provenance and development

Frictional is based on Nik Bear Brown's STEM Project Review implementation at `ac166a607d70b44e354cc7c142b9d78357887e71`, with remote fetch depth parameterized while keeping the original command's default. See [STEM baseline](STEM-BASELINE.md). No third-party project material, student records or real benchmark data are included. The tests use made-up projects: they check the tool's behavior, not student learning or validity in a course.

```sh
python3 -B -m unittest discover -v
```
