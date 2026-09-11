---
name: review_a11y_pr
description: Review accessibility related PRs.
---

# review_a11y_pr

Review code changes for accessibility issues.

## Instructions

1. Retreive all prs from https://github.com/flutter/flutter/pulls?q=is%3Aopen+is%3Apr+label%3A%22a%3A+accessibility%22+sort%3Aupdated-asc+-is%3Adraft+
2. for each pr, check the following:
  - If the the PR is created by chunhtai, skip.
  - If the PR is a draft, skip.
  - If the PR is approved, skip.
  - Whether the author has signed cla
  - if non googler, whether they have check the pr pre checks
  - Try if there is a counter example that are not fixed by the code. If so, come up with a test case to demonstrate it.
  - Whether the PR has a reviewer assigned.
  - If the previous question is yes, check whether the reviewer has comment on the pr within 2 weeks.
  - Whether the code trigger regression, if so, come up with a test case to demonstrate it. 
  - Whether the code can be simplified or or refactor to be more readable and maintainable.
3. Out put the result in a txt file in the project root
