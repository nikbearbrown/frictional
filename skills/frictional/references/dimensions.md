# Frictional dimension cards, version 1

Use this catalog for an authorized static audit. The IDs are stable, not a ranking. “Input” is the evidence needed to make the proposed observation, not a demand to gather private material. If it is absent, state the limit. Examples below are invented, not drawn from submissions. No card supplies a grade, an integrity judgment, or a claim about a named person.

## Artifact evidence

### artifacts
- **Purpose:** Inventory the deliverables promised and present at the pinned snapshot.
- **Input:** Assignment or release requirements, tracked paths, version identifiers, artifact types, and relevant checksums for authorized files.
- **Confound:** A file name or screenshot may not identify the build shown; external or intentionally omitted deliverables are not necessarily missing.
- **Example:** The guide references a sample report and source archive; only the source archive is in the reviewed revision. Report whether the report link was available separately.

### purpose-scope
- **Purpose:** Compare the stated problem, intended use, boundary, and acceptance conditions to what the artifact addresses.
- **Input:** Problem statement, requirements, claims, intended users, exclusions, success criteria, and identifiable outputs.
- **Confound:** An incomplete scope statement does not prove that the implementation lacks a feature; a demo is not a deployment claim.
- **Example:** An offline simulator models one warehouse zone; the report states that it has not modeled multi-site routing.

### outside-in-workflow
- **Purpose:** Trace a real use case from inputs and setup to outputs and an observable acceptance check, before assessing internals.
- **Input:** Authorized requirements, entry point, input and output examples, and a documented acceptance path.
- **Confound:** Tracing documentation and code does not prove the path ran. An internal unit test is not an end-to-end check.
- **Example:** Follow a synthetic CSV from the documented upload field to the named report column; identify where the acceptance example stops.

### scientific-validity
- **Purpose:** Check alignment of question, design, measurement, comparison, uncertainty, and claim.
- **Input:** Study question, units, protocol, controls or comparators where needed, measurement definitions, relevant results and limits.
- **Confound:** Repeatability does not remove selection bias, construct error, or confounding. Not all designs require controls, hypothesis tests, or the same sample sizes.
- **Example:** A comparison changes both speed and payload; it supports a comparison of the two settings, not an independent speed effect.

### data-evaluation
- **Purpose:** Inspect how inputs, comparison sets, failure modes, and uncertainties are defined and reported.
- **Input:** Data provenance and permissions, sampling and split rules, transformation record, metric formulas and denominators, baselines, leakage checks, and missing-data handling.
- **Confound:** A curated demo is not a target-population sample; absence of evidence in an available report is not evidence the evaluation was never done.
- **Example:** A classifier reports accuracy but omits the denominator and class mix; note the missing counts before interpreting the result.

### reproducibility
- **Purpose:** Determine whether a separate authorized reviewer could reconstruct the claimed artifact or result from a specified release and inputs.
- **Input:** Exact version, environment and dependency lock, commands, inputs or lawful access path, seeds, expected outputs, and tolerances.
- **Confound:** Pinning and documented commands support a reproducibility claim but are not a rerun. Proprietary or unavailable inputs can limit verification without invalidating the study.
- **Example:** A fixture and expected checksum are documented, but the data snapshot date needed for the reported result is unavailable.

### correctness
- **Purpose:** Compare inspectable behavior and logic to explicit contracts, including edge cases and invariant preservation.
- **Input:** Specification, relevant source paths and fixtures, expected boundary behavior, assumptions, and independently supplied results where available.
- **Confound:** Static reading can show an apparent discrepancy but not its runtime frequency or exploitability. Generated or unreachable files may not affect the reviewed path.
- **Example:** The specification expects empty input to produce an empty report; the visible path returns an error before the report builder.

### architecture
- **Purpose:** Inspect boundaries, data/control flow, dependencies, and costs against the artifact’s requirements.
- **Input:** Architecture diagrams or traceable source, entry points, service/component interfaces, dependencies, and known constraints.
- **Confound:** Tool count, language choice, or framework complexity alone does not establish robustness, maintainability, or performance.
- **Example:** A parser and exporter share a mutable cache; record the interface dependency and identify the assumption that a single writer uses it.

### verification
- **Purpose:** Distinguish test designs from actual results and inspect what important paths were checked.
- **Input:** Acceptance criteria, assertions or protocols, representative cases, and any redacted run records pinned to target revision and environment.
- **Confound:** Test files or a green badge can reflect only part of the target. Self-report and an independently attested run are different evidence types.
- **Example:** Unit tests cover normal inputs; revision-matched records for empty data and refresh recovery are unavailable.

### safety-security
- **Purpose:** Identify bounded source-level risks and missing controls for human safety, reliability, privacy, or information security.
- **Input:** Threat or risk assumptions, trust boundaries, permissions, secret-handling and fail-safe documentation, inspectable safeguards, and redacted approved evidence.
- **Confound:** A static concern is not proof of exposure, exploitability, harm, or noncompliance. Do not reproduce, scan, probe, or run a target.
- **Example:** An administrative action has an access check in the UI; the server-side authorization test is not shown. Flag the unverified safeguard without giving exploitation instructions.

### documentation-licensing
- **Purpose:** Inspect the documentation and trace ownership, permissions, attribution, and third-party terms.
- **Input:** Versioned operating and reference docs, license files, notices, attribution, permission records, and source provenance.
- **Confound:** An open source repository does not grant rights to all bundled data, images, or upstream components; this is not legal advice.
- **Example:** The source archive has a code license, but no asset notice accompanies a bundled sample image.

## Process evidence

### commit-history
- **Purpose:** Describe visible revision sequence and the kinds of changes preserved in history.
- **Input:** Authorized Git history, branch or PR scope, full or shallow history boundary, and diffs linked to the review snapshot.
- **Confound:** Squashing, rebasing, offline work, shared accounts, and generated imports can change apparent sequence and authorship.
- **Example:** One merge contains parser changes and tests; the reviewed branch does not preserve the preceding incremental commits.

### push-timeline
- **Purpose:** Distinguish platform-observed pushes or hand-ins from local commit dates and compare to an authorized, explicit deadline.
- **Input:** Platform-side events, exact ref and event scope, UTC-normalized timestamps, stated deadline and timezone, and coverage limits.
- **Confound:** Author dates, committer dates, imports, CI times, and file modification times are not push receipts. Even a known push time does not prove assessment receipt, late work, or effort.
- **Example:** A commit date is before the supplied deadline; without server or submission logs, push timing cannot be determined.

### install-test
- **Purpose:** Summarize readiness documentation and separately attest any independent install/run evidence.
- **Input:** Setup instructions, lockfiles and environment constraints; for attestation, an authorized external-sandbox record with exact revision, commands, environment, timestamp, outcome, and relevant logs.
- **Confound:** A clean setup on one machine does not prove portability; documentation, CI badge, or contributor testimony is not a substitute for the defined independent attestation. The audit never executes the target.
- **Example:** Instructions and dependencies were observed, but no scoped external install record was supplied; report setup documentation separately from the unverified install.

### readme-completeness
- **Purpose:** Check whether the front door explains what the artifact does and how a reader can use or inspect it.
- **Input:** README or designated landing document, supported routes/platforms, setup, examples, known limits, and repository links.
- **Confound:** Details may intentionally live in linked docs, or a submission spec may dictate a minimal README; length is not completeness.
- **Example:** The README points to a valid design note but neither document names the expected fixture output.

### naming-conventions
- **Purpose:** Compare file, branch, artifact, or symbol names with the project’s stated conventions and consumers’ requirements.
- **Input:** Written naming rules, tracked paths, and any import/build/reference contracts readable without execution.
- **Confound:** Case-sensitive deployments, framework conventions, and generated names may constrain choices; names do not reveal competence or authorship.
- **Example:** The specification points to `review-summary.json`, but the tracked output is named `review_summary.json`; do not infer whether any runtime resolves the mismatch.

### repo-hygiene
- **Purpose:** Check the reviewable snapshot for accidental clutter, misplaced generated content, untracked dependencies, or exposure risks.
- **Input:** Tracked tree, applicable ignore rules, deliverable boundaries, and scoped static metadata checks without exposing secret values.
- **Confound:** An intentional fixture, lockfile, or export can appear generated; an ignored file may be essential local input or a private asset.
- **Example:** Build-cache paths are tracked despite a stated source-only policy. Record the paths or aggregate count, not contents.

### template-delta
- **Purpose:** Describe project changes relative to the exact authorized starter, where one exists.
- **Input:** Starter path/revision, comparison scope, target revision, diff summary, generated/vendor exclusions, and rename handling.
- **Confound:** A large diff can be formatting or generated output; a small diff can implement the required behavior. No starter means no defensible delta.
- **Example:** Against the supplied release tag, changes are limited to a decoder and two tests after excluding generated outputs.

### contribution-distribution
- **Purpose:** Describe how attributable changes appear distributed across available records, with documented source limits.
- **Input:** Authorized PR/commit records, available co-author or handoff evidence, scope and denominator; aggregate or pseudonymize whenever possible.
- **Confound:** Recorded authorship is not labor allocation. Pair work, mentorship, editing, local work, squash merges, aliases, and shared accounts can be invisible.
- **Example:** One reviewed PR combines several contributions and lists one committer; the audit cannot allocate work from that alone.

### process-log
- **Purpose:** Trace dated steps, decisions, artifacts, and checkpoints that the available process record documents.
- **Input:** Authorized logs, decision notes, issue/PR records, redacted execution evidence, and revision-linked artifacts.
- **Confound:** Narrative can be retrospective; unlogged actions may have occurred, and log specificity is not proof of independent execution.
- **Example:** A log reports dropping an input format and identifies the corresponding removal diff; reason remains reported unless corroborated.

### verification-record

- **Card version:** 1.
- **Checks:** whether an explicitly cited historical run matches the recorded revision, covers the claimed main path, and has a usable result.
- **Inputs:** minimal normalized run attestation and cited ID. This does not re-run or independently verify the attestation.
- **Confounds:** independent checks can be unrecorded; a current local tree can differ from HEAD; a passing run establishes only its stated coverage.
- **Example:** a cited run reports success, but its recorded scope did not exercise the main path. Flag the scope mismatch without attributing intent.

### error-trajectory
- **Purpose:** Trace documented problem, attempted fixes, result, and remaining uncertainty without inferring intent or ability.
- **Input:** Error record or symptom, revisions, repro instructions supplied as text, and relevant validation evidence; do not execute the target.
- **Confound:** Error counts depend on logging coverage, test order, changing inputs, and opportunities to record. Silence does not prove a fix.
- **Example:** A sample failed under configuration A; the later record shows a passing result under B, but no comparable verification of A.

### temporal-engagement
- **Purpose:** Describe the span or cadence of visible workflow evidence, where time is relevant, without claiming hours, attention, or diligence.
- **Input:** Authorized, scope-limited dated events, event types, timezone, aggregation window, and any known gaps.
- **Confound:** Offline work, accessibility accommodations, scheduling, merges, retrospective logs, and timezone conversion alter event cadence. Do not profile individuals.
- **Example:** Visible changes appear on two dates; no activity measure is inferred for the intervening days.

### transfer
- **Purpose:** Check whether evidence addresses reuse of a method in a meaningfully different task or setting.
- **Input:** Explicit source and transfer tasks, preserved constraints, identifiable change in setting, outcome criteria, and actual result if claimed.
- **Confound:** Repeating the same example or swapping superficial labels is not evidence of generalization. A single new instance does not establish broad transfer.
- **Example:** An explanation adapts a queue rule to a different input order; observed behavior on the new case remains unverified.

### calibration
- **Purpose:** Compare a documented confidence or prediction to the scoped evidence that later became available.
- **Input:** Dated prediction or confidence statement, stable target event and horizon, and a matched outcome or reference.
- **Confound:** Retrospective explanations are not pre-event confidence; a single prediction cannot estimate statistical calibration. Do not infer personal metacognition.
- **Example:** A pre-run estimate predicted 20 minutes and a matched attestation shows 27; the report records this case, not general calibration.

### retrieval
- **Purpose:** Distinguish a documented use or explanation of a concept from evidence of recalling it under known conditions.
- **Input:** Authorized task instructions, access constraints, prompt or retrieval conditions, response, and any relevant source disclosure.
- **Confound:** A correct answer can arise from reference use, collaboration, tools, or recall. Code and style alone cannot establish which.
- **Example:** An explanation correctly identifies a feedback loop, but no record shows whether references were allowed or consulted.

### scaffolding
- **Purpose:** Describe documented support such as a starter, template, hint, example, or feedback and which parts of the task it supports.
- **Input:** Supplied starter and instructions, approved help and example materials, timestamps where relevant, and scoped deltas.
- **Confound:** Similarity to a starter may be expected; absent scaffolding records do not prove solo or unaided work. Do not infer tool use from code style.
- **Example:** A starter supplied chart axes and labels; the changed data transform is a separate observation, not a claim about how it was produced.
