---
trigger: always_on
---

# Generated artifacts

This repo is a skeleton of agent configuration. Only hand-authored files
belong in the committed tree: skills, rules, and the code that implements
them.

Anything a script or an agent generates goes in `tmp/`, which is gitignored:

* raw API dumps, scraped data, fetched JSON
* reports, summaries, and other regenerated markdown
* caches, indexes, queues, scratch files
* machine-generated skills

Use a subdirectory named after whatever produced it, for example
`tmp/fetch_flutter_discussions/`.

Never write a generated file to the repo root. When a script takes an output
path, resolve relative paths against `tmp/` rather than the current directory,
so a bare filename cannot land in the working tree.

The remote is public. Treat `tmp/` as the only place output may go, and never
`git add -f` anything from it.
