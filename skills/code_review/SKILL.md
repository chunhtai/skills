---
name: code_review
description: Review a given PR.
---

# code_review

Review code changes for a given PR.

## Parameters

- **url** (string, required): URL to a github pr

## Instructions

1. Whether the author has signed cla
2. if non googler, whether they have check the pr pre checks
3. Try if there is a counter example that are not fixed by the code. If so, come up with a test case to demonstrate it.
4. Whether the code trigger regression, if so, come up with a test case to demonstrate it. 
5. Whether the code can be simplified or or refactor to be more readable and maintainable.
6. Whether the test is really testing the right thing. Whether it will fail if without the code change.
