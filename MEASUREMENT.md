# Measuring Frictional dimensions without creating a grade

An audit records evidence about the project and separately describes work traces. It does not combine those measures into an overall score. Report the scope, window, evidence origin and denominator before interpreting a count.

| Dimension | Record | Keep separate |
| --- | --- | --- |
| STEM quality | Requirements tested, representative path, actual environment and result, claim and evidence; reviewer interprets fit and material gaps | Tests discovered vs tests executed vs behavior verified; statistical/methodological validity vs clear presentation |
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
