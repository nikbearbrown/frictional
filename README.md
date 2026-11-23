# Frictional

A read-only skill for reviewing **what a STEM project accomplishes** and **what the available evidence shows about how it was made**. It produces one evidence record in two forms: `report.md` for a person and `report.json` for a tool. It does not grade, score genuine learning, judge authorship, or make an academic-integrity decision.

## Start here

Python 3.10+ is required. Git is required for an explicitly requested GitHub fetch and certain history checks. There are no runtime Python packages to install.

```sh
./frictional audit /path/to/project --out /tmp/frictional-review
./frictional audit https://github.com/OWNER/REPO --out /tmp/frictional-github --history-depth 100
./frictional validate /tmp/frictional-review/report.json
./frictional render /tmp/frictional-review/report.json --out /tmp/frictional-render
```

Use a new directory outside the reviewed folder, with an existing parent. `--ref` pins a GitHub checkout to a full commit SHA. GitHub retrieval is time-bounded and limited to the opted-in repository, not byte-capped; for a large repository use a local checkout. Pass an explicit, bounded synthetic or authorized normalized file with `--evidence`, a comparison folder with `--starter`, and a timezone-qualified ISO timestamp with `--deadline`. The [general profile](profiles/general.json) hides nothing; an instructor can opt out of dimensions explicitly.

For a substantive audit, give a trusted agent [the skill](skills/frictional/SKILL.md) and the target. The CLI is static intake and deterministic evidence collection, not an independent scientific review. It will find conventional tests and install instructions but won't test whether they work. The agent can add source-linked interpretation with its actual model name and version, then render the validated record again. Never execute target code, probe deployment, change the project or post findings automatically.

## Read the evidence in two streams

- **Artifact:** the existing STEM rubric and CLI are retained. Scope, reproducibility, correctness, tests, architecture, documentation, licensing and scientific validity still matter. `./stem-review` remains available when the full original STEM-format output is wanted. Frictional turns those intake observations into neutral statements; major lens judgments remain unassessed without substantive review.
- **Process:** describe visible commits, supplied push events, documented checks, repository conventions, starter comparison and supplied course observations separately. Commit dates are editable, can omit offline work, and never establish hours worked. A push is not a commit. A sparse history or missing record never lowers a grade.
- **Evidence status:** every default dimension is visible. `no_evidence`, `blocked`, `not_assessed`, and `hidden_by_instructor` are different states, not negative results. A reported observation can have alternative explanations. A caller-supplied receipt is not an independent rerun.

See the [measurement rules](MEASUREMENT.md), [dimension cards](skills/frictional/references/dimensions.md) and [JSON schema](frictional.schema.json).

## Privacy

Frictional sends no data to Irreducibly Human and has no telemetry. A GitHub URL causes an explicit fetch from GitHub; a local audit does not need network access. A separate agent or hosting platform may process data according to its own rules. Do not supply raw LMS records, transcripts, real names, grades, or secrets to this prototype. Evidence JSON is deliberately narrow and caller-supplied. Do not develop or test with student work. Inspect and redact reports before sharing. Never run install or test commands from an untrusted repository on the host. This version accepts normalized external receipts only, not commands or raw transcripts.

## Provenance and development

Based on the user’s STEM Project Review implementation at `ac166a607d70b44e354cc7c142b9d78357887e71`, with remote fetch depth parameterized while keeping the original command's default. See [STEM baseline](STEM-BASELINE.md). No third-party project material, student records, or real benchmark data are included. Review rights to the private baseline before any external release. Synthetic regression tests test the tool’s behavior, not student learning or validity in a course.

```sh
python3 -B -m unittest discover -v
```
