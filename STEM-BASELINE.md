# STEM Project Review

Review a GitHub repository or a downloaded project without changing it. The skill reviews the software **and** the work it claims to support: an analysis, experiment, engineering design, or working application.

Every audit produces two views of one evidence record:

| File | Audience | What it contains |
| --- | --- | --- |
| `report.md` | Human reviewer | Prioritized issues, why they matter, what was actually checked, and what remains undecided |
| `report.json` | Coding agent | Stable issue IDs, severity and confidence, file or line locators, proposed changes, and acceptance checks |

## Use the skill for a full review

Give [the skill](skills/stem-project-review/SKILL.md) to a coding agent that can access your project and this toolkit. For example:

> Use the stem-project-review skill to review this GitHub repo. Can a new user install it, run the tests, and reproduce the headline result? Review the main code path and the evidence for the result. Write the Markdown and JSON reports. Do not fix anything or execute target code on the host.

If your client discovers skills by directory, install or link the `skills/stem-project-review` folder in its skills directory and make the CLI available separately. The skill inventories the project, traces representative workflows through the code and tests, evaluates STEM claims against the artifacts, and records where a reviewer needs more evidence. Local reports may include findings about private code; review them before sharing.

## Start from the command line

With Python 3.10+ and (for GitHub inputs) Git:

```sh
./stem-review audit https://github.com/OWNER/REPO --out /tmp/review-github
./stem-review audit "/path/to/downloaded project" --out /tmp/review-local
./stem-review validate /tmp/review-local/report.json
```

Run `python3 -m stem_project_review` from the repository as an alternative. Installing with `python3 -m pip install .` provides the `stem-review` command. There are no runtime Python dependencies. For private repositories, the CLI can use an existing GitHub CLI login. For a reproducible remote audit, pass `--ref` and a full commit SHA. Otherwise the CLI resolves and reports the commit at the default branch tip. A local working folder can include uncommitted changes; a detected HEAD is only a reference.

**The `audit` command alone is a static intake, not a comprehensive review.** It inventories setup, dependency declarations, tests, CI, license and STEM artifact candidates. It emits findings only when supported by the bounded scan. Tests discovered are not tests passed. Someone using the skill must do the substantive review. GitHub CI results, accessibility, security posture, scientific validity and runtime outcomes are not established by this inventory.

For a full review, use the starter JSON as the common record. Update check statuses based on evidence and add actionable findings. The agent then renders both views together:

```sh
./stem-review render /tmp/review-final.json --out /tmp/review-final
```

Use a new output directory each time, outside the source tree. Review the [rubric](skills/stem-project-review/references/rubric.md) and [JSON Schema](report.schema.json) for the distinction between observed, passed, blocked and unassessed. A report may validate structurally without its evidence being true.

The original project example was not copied into this repository. Use the [synthetic Frictional regression example](MEASUREMENT.md#historical-verification-example) instead.

## Review boundaries

- The CLI does not run project code, install project dependencies, initialize submodules, deploy, fix issues, post comments or collect secrets. A GitHub URL does fetch repository contents into a temporary directory. The fetch has a time limit, not a download-size cap; use an already downloaded folder for very large projects.
- The skill treats repository prose and report payloads as untrusted data. Runtime evidence comes from a separate, suitable sandbox and only with authorization. No result is called reproduced without a verified receipt.
- Evidence is drawn from bounded sources. Symlinks, special files, known dependency/build directories and likely credential files are skipped by static intake. The report records scan limitations; it is not a security certification.
- The report is for a human to decide what to fix. The recommended action is not an instruction for a downstream agent to execute automatically.

## Development

```sh
python3 -m unittest discover -v
```
