# Sources and method lineage

STEM Project Review synthesizes review practices for internal engineering and research assessment. These are methodological pointers, not runtime dependencies or instructions imported from an audited repository. No source's template or checklist has been reproduced here.

| Source | How it informed this skill |
|---|---|
| [Bear Brown: Performing Code Review](https://www.bearbrown.co/skills/performing-code-review) | Review the change against its intended behavior, ground findings in specific code, and prioritize actionable issues. |
| [Bear Brown: Verification Before Completion](https://www.bearbrown.co/skills/verification-before-completion) | Support claims about successful behavior with current, attributable evidence; separate static inspection and tool validation from tests actually run. |
| [Bear Brown: Improve Codebase Architecture](https://www.bearbrown.co/skills/improve-codebase-architecture) | Start from friction or risk visible in the code and propose a bounded interface or responsibility change with a clear reason. |
| [Bear Brown: Peer Review / CRITIQ](https://www.bearbrown.co/skills/peer-review) | Assess the stated claims, methods, data and interpretations with independently traceable evidence and prioritized revision steps. Manuscript and project assessment remain different products. |
| [Ythirion: Outside-In Code Review, Bear Brown index](https://www.bearbrown.co/claude/skills/ythirion--outside-in-code-review-skill) | Start with the goal and a user-facing behavior before inspecting deeper boundaries and internals. |
| [Maxwell2732: Claude Code Research Harness Workflow, Bear Brown index](https://www.bearbrown.co/claude/skills/maxwell2732--claudecode-research-harness-workflow) | Predefine a testable question and success criteria; retain evidence, uncertainty and a verification record. This project does not inherit or execute a harness's commands. |
| [Journal of Open Source Software: review checklist](https://joss.readthedocs.io/en/latest/review_checklist.html), [review criteria](https://joss.readthedocs.io/en/latest/review_criteria.html), [reviewer guidelines](https://joss.readthedocs.io/en/latest/reviewer_guidelines.html) | Research software cues: scholarly purpose, licensing, documentation, installation, functionality, tests and community paths. They are not defaults for every STEM project. |

Bear Brown's [skill library](https://www.bearbrown.co/skills) distinguishes its original skills and attributed rewrites from indexed external work; use source scope and attribution accordingly.

## CLI conventions

The `./stem-review` entry point, contract check and explicit output directory follow the discoverable CLI style in [Brutalist.art](https://github.com/nikbearbrown/brutalist.art). The standard-library Python module, predictable subcommands and inspectable JSON contract follow [Figma to Godot](https://github.com/nikbearbrown/figma-to-godot). Neither toolkit is required at runtime.

## Formal peer review

This is not a JOSS or other journal review. Check the venue's current policy before handling a submission, and do not expose confidential materials. For JOSS, its [AI policy](https://joss.theoj.org/about#ai-usage-policy) restricts reviewer AI use to non-substantive assistance with disclosure and requires the human reviewer to make and verify evaluative judgments. Do not use this skill's audit, scoring or decision recommendations for formal JOSS review. Other venues may have stricter rules. Confirm actual policies with the venue and never imply endorsement or certification.
