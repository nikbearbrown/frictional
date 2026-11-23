# Review rubric

Use these lenses as applicable. Match depth to stakes. An exploratory notebook, deployed product, benchmark and publishable research package should not share the same release bar. For every lens record coverage, important observations and verification gaps; use not applicable only with an explanation.

| Lens | What to inspect | Questions to answer |
|---|---|---|
| Purpose and scope | README, intended users, examples, claims, supported environments, maturity | Is the problem and claimed contribution clear? Does the user journey actually answer it? What is excluded? |
| Outside-in workflow | Install docs, configuration schema, entry points, sample inputs, outputs, errors | Can a first-time user infer how the system works without hidden state? Does the main path agree across docs, implementation and tests? |
| Scientific and mathematical validity | Definitions, assumptions, units, claims, references, experimental design, analyses | Does evidence support the precise claim? Are controls, baselines and uncertainty appropriate? Are causal claims separated from correlation? |
| Data and evaluation | Source versions, rights and consent, preprocessing and split boundaries, metrics, benchmark protocol | Are data provenance, exclusions, leakage risks, selection effects and limitations documented? Are reported metrics comparable and reproducible? |
| Reproducibility and portability | Pinned revisions, environments, seeds, data manifests, artifacts, dependency and hardware constraints | Can an independent reviewer locate inputs/outputs and understand nondeterminism, prerequisites and costs? Which parts are inaccessible? |
| Correctness and resilience | Core invariants, boundaries, errors, parsing, retries and data handling | Which realistic conditions break correctness or silently corrupt outcomes? Where do failures become visible? |
| Architecture and sustainability | Dependency direction, responsibilities, public interfaces, ownership, configuration, duplication | Can relevant changes be localized and tested? Does a proposed redesign solve demonstrated friction rather than aesthetic preference? |
| Verification and operations | Unit/integration tests, negative cases, CI definition and trusted status, observability, release and rollback notes | What evidence covers behavior at this revision? Are tests assertions or merely fixtures, and are logs/results attributable to the same snapshot? |
| Safety, security and responsible use | Privilege and trust boundaries, data exposure, threat assumptions, disclosure path, human approval, domain hazards | What credible harm does the design enable? What defensive controls reduce it? Do not run attacks or give operational misuse details. |
| Documentation, inclusion and licensing | Tutorials, API docs, accessibility, contribution paths, citation, license and assets | Can intended audiences use and maintain the project? Are authorship, reuse rights and limits explicit? Absence of a license file is not a legal opinion. |

## Evidence and decisions

Treat **observed static evidence**, **observed eligible isolated-run evidence**, **external/author claim**, **inference**, and **unknown/not checked** as distinct. A document's claim is observed as text, not necessarily as fact. Give stable references (`path:line`, pinned URL, report/test ID or output digest) whenever available. Note whether each material finding is high, medium or low confidence and state the reason. No match in a bounded search is not global absence. Report conflicting evidence separately and explain which interpretation is supported.

Recommended finding headings:

- **ID and severity:** critical (credible severe harm or core claim invalidated), high (material blocker), medium (meaningful defect with bounded impact), low (minor/optional). Severity is not confidence.
- **Snapshot and evidence:** exact paths or versioned sources, what was actually seen, and further evidence needed.
- **Impact and uncertainty:** affected workflow/result, conditions, tradeoffs, plausible counterexamples, and confidence.
- **Remediation:** safest useful change, suggested owner, acceptance check, residual risk, and dependencies.
- **Human decision or agent-ready task:** identify approval gates and actions intentionally left pending. Give agent-ready tasks narrow file scope, do-not-change constraints, expected behavior and stop/handoff triggers; the review itself is not authorization to execute.

End with a qualified disposition: ready for the stated use, ready with conditions, not ready, or insufficient evidence. Name the controlling findings and checks that could change the decision. A recommendation is not a certification, peer-review acceptance, or assertion of compliance.
