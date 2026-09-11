---
name: search-conversation-memory
description: Search past conversations for prior work on a topic. Use when a question refers to something decided earlier, mentions an unfamiliar identifier or error string, asks "did we already", "last time", "as we discussed", or when a task looks like a repeat of previous work. Also use when the automatic memory injection surfaced a topic and the summary is not enough detail.
---

# Search conversation memory

An automatic hook already injects up to three matching summaries whenever you
send a new message. Use this skill when that was not enough: the hook returns
three hits at most and shows only summaries.

## Search

```bash
python3 ~/git/skills/memory/scripts/search.py "focus traversal semantics"
```

Widen a search that returns nothing:

```bash
python3 ~/git/skills/memory/scripts/search.py --min-score 0.5 --limit 10 "query"
```

See everything in the index:

```bash
python3 ~/git/skills/memory/scripts/search.py --list
```

Matching is word overlap against each entry's title, keywords, and aliases,
weighted so rare words count for more. It is not semantic. Words are stemmed,
so "overflowing" finds "overflow", but an unrelated synonym will not match. If
a search misses, retry with the literal identifiers, error text, file
basenames, or command names involved.

See which entries have been useful when they were injected:

```bash
python3 ~/git/skills/memory/scripts/feedback.py --report
```

## Read the full history

Each hit lists the transcript paths for the conversations behind it. Summaries
are lossy, so read the transcript when the detail matters:

```bash
grep '"type":"USER_INPUT"' /path/to/transcript.jsonl
```

The `transcript.jsonl` file is truncated for large outputs. Its sibling
`transcript_full.jsonl` maps line for line and holds the complete content; read
individual lines from it rather than the whole file.

## Treat results as leads, not facts

Entries are dated. An entry describing code can be wrong because the code moved
on. Verify against the current source before acting on anything retrieved. When
a retrieved conclusion conflicts with what the code says now, the code wins and
the entry is stale.

## Record something worth keeping

The digest sidecar writes entries automatically every four hours. Write one by
hand only when the user asks you to remember something specific, or when a
conclusion was expensive to reach and the conversation is about to end:

```bash
echo '{
  "id": "topic-slug",
  "title": "Short title",
  "summary": "Two or three sentences.",
  "points": ["concrete conclusion", "gotcha worth knowing"],
  "keywords": ["literal_identifier", "error text", "flag-name"],
  "aliases": ["how you would describe this before knowing the name"],
  "workspaces": ["/absolute/path/to/workspace"],
  "files": ["/absolute/path.dart"],
  "conversations": ["<this conversation id>"],
  "corrections": []
}' | python3 ~/git/skills/memory/scripts/upsert.py
```

Reusing an existing `id` merges into that topic instead of creating a
near-duplicate. Run `--list` first to check.

The `keywords` field decides whether the entry is ever found again. Put the
literal strings a future question would contain, not a description of them.

`aliases` covers the opposite case: the words someone would use who has not
yet learned the identifier. For an entry about `RenderFlex overflowed`, that
is "yellow black stripes" or "row too wide".
