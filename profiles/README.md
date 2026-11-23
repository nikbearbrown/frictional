# Frictional field profiles (version 1)

The general profile enables all 26 [artifact and process dimensions](../skills/frictional/references/dimensions.md). A field profile is optional. Its only operational setting is `hide`, an array of exact dimension IDs. An empty array keeps all dimensions visible. It never changes evidence rules, computes weights or scores, or alters the audit’s authority.

```json
{
  "name": "General",
  "hide": []
}
```

`name` is an optional display label. Optional `cip` metadata identifies a Classification of Instructional Programs field when a maintained, verified profile needs one; store the code as a string, retaining its punctuation and leading zeroes. CIP is descriptive metadata, not a reason to auto-select a review profile or to hide evidence.

Choose a profile based on the artifact and review question, not a person, team, grade, title, or assumed disciplinary norm. Distinguish hidden dimensions (intentionally out of this view), not applicable dimensions (reviewed for relevance and found inapplicable), and unavailable evidence (relevant but not accessible). State the profile and hidden IDs in the audit scope, and disclose why any customized exclusions were chosen. Unknown IDs and duplicates should be corrected before relying on a profile. If no specific profile is needed, leave everything enabled and explain nonapplicability per dimension.

The included general profile is the conservative default. Future field profiles must be based on published task requirements or synthetic examples, never student submissions or inferred individual traits.
