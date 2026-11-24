# Measuring Frictional dimensions without creating a grade

An audit records evidence about the project and separately describes work traces. It does not combine those measures into an overall score. Report the scope, window, evidence origin and denominator before interpreting a count.

| Dimension | Record | Keep separate |
| --- | --- | --- |
| STEM quality | Requirements tested, representative path, actual environment and result, claim and evidence; reviewer interprets fit and material gaps | Tests discovered vs tests executed vs behavior verified; statistical/methodological validity vs clear presentation |
| Container run (install, run, test, serve) | Commands with their README sources, exit codes, seconds and masked logs, in a fresh Linux container | A clean container vs. the author's machine; guessed commands vs. documented ones; tests passing vs. the main path working |
| Test history (Y2) | Test outcome at each of the last N commits, failure categories, transitions | Current dependencies vs. those at the time; tests added late; error coherence needs a reader |
| Course files | FRICTIONAL.md rubric rows filled, dates vs. commits, PREDICTIONS.md calibration arithmetic, VERIFICATION.md and CONTRIBUTIONS.md fields | Filled vs. specific; the student's account vs. what happened; no text retained |
| Commit history | Bounded visible first-parent count, covered revision and coverage, distinct UTC activity-date labels, time span, timestamp gaps and deadline window when supplied | Time on task, side-branch effort, changes not in Git, squashed or rebased work |
| Push and team history | Supplied server events; distinct recorded author metadata and aggregate distribution | Git dates vs server arrival, contributors vs people who committed, no individual ranking |
| Verification evidence | Cited run, revision, test scope and result | Checking now vs a historical record; passing irrelevant tests; concise supported notes vs unsupported prose |
| Engagement / timing | Authorized milestone intervals, counts and supplied difficulty bands; record timing source and idle gaps | Elapsed time, active time and learning are not interchangeable |
| Error trajectory | Ordered relevant run outcomes, failure categories and transitions; document comparable scope/environment | Diagnostic progress vs unrelated environment drift; errors alone do not measure learning |
| Transfer | Separate near/far items on an unassisted new task with common rubric and distinct outcome; expose denominators | Don't feed the same task or its results back into predictor variables when validating |
| Calibration | Pre-feedback confidence matched to graded correctness; count, mean confidence and mean squared probability error when supplied | Item mix, very small samples, and systematic over- or underconfidence; no learner label |
| Process log / social texture | Presence and date/commit linkage; if authorized, an observer can assess explicit rationale, evidence and revision | A log is a self-report unless corroborated; never use writing fluency to infer practice, identity or competence |
| Retrieval | Descriptive success by delay, with comparable items and known conditions | Delay-bin counts are not forgetting or a spacing effect without a valid design |
| Scaffolding | Descriptive success by recorded hint level | Ability to choose hints, difficulty and ordering confound the curve; no causal effect from grouped counts |

The [dimension cards](skills/frictional/references/dimensions.md) spell out other limitations. The typed input contract for the optional course and repository-process fields is in the docstring of [process_evidence.py](process_evidence.py); malformed packets are blocked, not silently turned into zero. No course or push data is fetched automatically. First-parent history reads up to 250 commits; packed history has a 16 MiB local pack/index budget. A sparse or truncated history is reported with coverage, not interpreted as the whole project. Per-commit change sizes and file/line attribution are not implemented in v0.1; they require a bounded diff definition and rules for merge commits and generated files. Some measures, like quality of reasoning and tests exercising a particular claim, require human or model interpretation rather than a filename or commit count.

### Historical verification example

Supply a normalized attachment only for records you can inspect and are authorized to summarize. A run ID is a short local pseudonym, not a person's identity. The audit compares the record with the target's known Git revision. It does not look up arbitrary URLs, read logs, independently authenticate the record, or imply a dirty working tree was tested.

```json
{
  "verification-record": {
    "claimed_run": "run-2",
    "runs": [
      {
        "id": "run-2",
        "revision": "1111111111111111111111111111111111111111",
        "covers_main_path": true,
        "outcome": "passed"
      }
    ]
  }
}
```

The synthetic regression tests hold the artifact fixed while varying the record: a supported check, no historical evidence, a cited run that misses the main path, and an equally supported minimal record. Equal evidence gets equal treatment regardless of prose length. These are tool checks, not benchmark or classroom findings.

## The seven GLP components

Every report includes a table mapping the preprint's components (Y1 to Y7) to the dimensions that can carry evidence for each, and marks four of them (Y1, Y3, Y6, Y7) as needing course data that a repository cannot hold. Where course data is supplied, the packets compute the preprint's own descriptive quantities: Y1's difficulty–time correlation, Y3's near–far gap and 0.4/0.6 weighting, Y4's Brier score and direction, Y7's (hint − none) / (worked − none) ratio. None is combined into a GLP score, and none is a validated measure: the preprint's validation study has not been done.

### Probe receipts

`frictional probe receipts` turns in-class probe receipts into the `calibration`, `transfer`, `retrieval`, `scaffolding` and `temporal-engagement` packets. One probe gives each student 6 to 8 items: too few for a per-student calibration (the study's minimum is 30 rated items), transfer gap (one near and one far item) or Y7 (one bug item). Retrieval rows describe success by delay since the deadline; they are not a spacing effect, and an individual Y6 needs a counterbalanced design the probe does not have. Hint-ladder data use `"design": "ladder"`, which reports success at each rung among those who reached it and never the preprint's ratio. Timed probe items use `"grain": "item"`; their times are cut off by the timeboxes.
