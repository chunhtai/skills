# Rules

Markdown rule files discovered alongside the skills in [`skills/`](../skills).

Each `*.md` file here may start with YAML frontmatter controlling activation:

```markdown
---
trigger: model_decision
description: "When to load this rule."
---

# Rule title

* Guideline one.
* Guideline two.
```

`trigger` values:

* `always_on` - injected every turn. Use sparingly.
* `model_decision` - listed by description; loaded when the model judges it relevant. Requires `description`.
* `glob` - loaded when an open or edited file matches. Requires `globs`, a comma-separated list such as `*.dart,*.md`.
* `manual` - loaded only when @-mentioned.

Rules without frontmatter are treated as plain context for the directory they live in.
