---
name: fix_go_router
description: Pick a go_router issues and fix it.
---

# fix_go_router

Fix go_router related issue.

## Instructions

1. Pick an easy go_router issue from https://github.com/flutter/flutter/issues?q=state%3Aopen%20label%3A%22p%3A%20go_router%22 that doesn't have an assignee.
2. Check whether there is another duplicated issue that has an assignee. If so, report back to me and stop here.
3. Add a test case in go_router package that can reproduce the issue.
4. Fix the code and verify the test case pass.
5. Add a pending_changelog in go_router package with a brief description of the fix.
6. Create a PR against main branch in https://github.com/flutter/packages, the pr body should include the issue number and a brief description of the fix.
