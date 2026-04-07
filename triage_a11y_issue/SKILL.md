---
name: triage_issue_a11y
description: Triage linked a11y issue
---

# triage_issue_a11y

Review linked issue and determine if it is a valid a11y bug report, feature request, or question.

## Parameters

- **issue_url** (string, required): URL of the issue to triage, or a page to a list of issues.

## Instructions

Add the entire codebase in context https://github.com/flutter/flutter

Review the provided issue and identify:
1. If no reproduceible example, come up with one by parsing the error message, stacktrace and use flutter codebase to come up with reproducible test case. The result should be a code block with <details> tag. If there is already a reproducible example, ignore this step
2. If the issue is accessibility related, determine the fix is in android, ios, web, desktop, or framework. If not, find out which team-* label it should use.
3. If the issue is a duplicate of an existing issue, link to it. If not, ignore this step.
4. Propose possible fix by providing code changes. If a code change is not feasible, provide explanation. The code change should be a code block with <details> tag.
5. Summarize the above in a comment and post it to the issue, keep them short and precise. The comment should start with "Response from AI assistant:"
6. Clean up the local changes made during the process.
