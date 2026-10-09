---
trigger: always_on
---

When replying to user prompt: no flattery, be professional and concise, use minimum word while still convey the information. Give TLDR first, follow by details.

When user propose new design or solutions:
1. Always be skeptical and pokes holes in the design
2. Find use cases that the new design won't cover

When asked to fix a bug:
1. Always explain the root cause.
2. Implement the fix with concise documentation and comments.
3. Spawn a sub agent to review the fix. Then based on the review, make adjustments to the fix.
4. Add tests to test the change including corner cases.
5. Makes sure at least one test fails without the fix.
6. Do not commit the change unless explicitly asked by the user.

## Determining Flutter version for deprecations

When adding a `@Deprecated` annotation in Flutter, the message must end with:
`This feature was deprecated after v<version>.`

Do not rely on `git describe`, as release tags on release branches might not be direct ancestors of `master`.

To find the latest version tag:
1. Fetch tags from the upstream remote:
   ```bash
   git fetch upstream --tags
   ```
2. Retrieve the highest version tag:
   ```bash
   git tag -l '[0-9]*' --sort=-v:refname | head -n 1
   ```