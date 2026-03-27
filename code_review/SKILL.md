---
name: code_review
description: Review code changes for quality, correctness, security, and style issues.
---

# code_review

Review code changes for quality, correctness, security, and style issues.

## Parameters

- **url** (string, required): URL of the code changes to review.

## Instructions

Review the provided code changes and identify:
1. Whether the author has signed cla
2. if non googler, whether they have check the pr pre checks
3. Try if there is a counter example that are not fixed by the code. If so, come up with a test case to demonstrate it.
4. Whether the code trigger regression, if so, come up with a test case to demonstrate it. 
5. Whether the code can be simplified or or refactor to be more readable and maintainable.


Provide clear, actionable feedback with specific line references where applicable.
