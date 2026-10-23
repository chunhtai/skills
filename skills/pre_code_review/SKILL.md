---
name: pre_code_review
description: Perform a pre-review assessment of a PR and its linked issue.
---

# pre_code_review

Evaluate a pull request and its linked issue before a full code review.

## Parameters

* `url` (string, required): URL to a GitHub pull request.

## Instructions

Inspect the pull request and its linked issue, and output the results in the following order:

1. Check the linked issue for the root cause and output a summary of the root cause.
2. Judge whether the issue needs to be fixed or is a user error.
3. Inspect the PR changes and judge whether the PR fixes the root cause or is only a band-aid fix.
4. Output a detailed summary of what the PR does.
