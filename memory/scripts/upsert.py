#!/usr/bin/env python3
"""Merge one memory entry into the index.

Reads a JSON object on stdin and merges it by `id`. List fields are unioned so
a topic accumulates conversations and keywords over time; `title`, `summary`
and `points` are replaced by the newest version.

This is also where the digest queue is confirmed. A conversation stays in the
in-flight file until an entry naming it lands here, or until --skip says it
was judged not worth an entry. Either way the digest agent has finished with
it and it will not be handed out again.

Usage:
    echo '{"id": "topic", "title": "...", ...}' | python3 upsert.py
    python3 upsert.py --skip <conversation-id> [<conversation-id> ...]
"""

import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

# Keep the index small enough that the retrieval hook stays fast and the
# system prompt budget is never at risk.
MAX_ENTRIES = 500

_LIST_FIELDS = ("keywords", "aliases", "workspaces", "files",
                "conversations", "points")


def merge(existing, incoming):
    out = dict(existing)
    for field in ("title", "summary"):
        if incoming.get(field):
            out[field] = incoming[field]
    if incoming.get("points"):
        out["points"] = incoming["points"]
    for field in ("keywords", "aliases", "workspaces", "files", "conversations"):
        merged = list(existing.get(field, []))
        for value in incoming.get(field, []):
            if value not in merged:
                merged.append(value)
        out[field] = merged
    corrections = list(existing.get("corrections", []))
    for correction in incoming.get("corrections", []):
        if correction not in corrections:
            corrections.append(correction)
    out["corrections"] = corrections
    # Feedback counters are earned by the topic, not by the summary, so
    # a re-summarised entry keeps its track record.
    for counter in ("useful", "useless"):
        out[counter] = int(existing.get(counter, 0) or 0)
    out["modified"] = memlib.now_iso()
    out.setdefault("created", existing.get("created") or memlib.now_iso())
    out.setdefault("accessed", existing.get("accessed") or "")
    return out


def normalize(entry):
    entry = dict(entry)
    entry["id"] = str(entry.get("id", "")).strip().lower().replace(" ", "-")
    for field in _LIST_FIELDS:
        value = entry.get(field)
        if value is None:
            entry[field] = []
        elif isinstance(value, str):
            entry[field] = [value]
    if not isinstance(entry.get("corrections"), list):
        entry["corrections"] = []
    return entry


def evict(entries):
    """Drop the least recently touched entries once over the cap."""
    if len(entries) <= MAX_ENTRIES:
        return entries
    entries.sort(key=lambda e: e.get("accessed") or e.get("modified") or "",
                 reverse=True)
    return entries[:MAX_ENTRIES]


def skip(conversation_ids):
    """Acknowledge conversations the digest agent decided not to record."""
    if not conversation_ids:
        print("error: --skip needs at least one conversation id", file=sys.stderr)
        return 1
    cleared = memlib.resolve_inflight(conversation_ids)
    print("ok: skipped %d, cleared %d from the queue"
          % (len(conversation_ids), cleared))
    return 0


def merge_into_index(incoming):
    entries = memlib.read_index()
    for i, existing in enumerate(entries):
        if existing.get("id") == incoming["id"]:
            entries[i] = merge(existing, incoming)
            break
    else:
        incoming["created"] = memlib.now_iso()
        incoming["modified"] = incoming["created"]
        incoming.setdefault("accessed", "")
        entries.append(incoming)
    memlib.write_index(evict(entries))


def main():
    if "--skip" in sys.argv:
        return skip([arg for arg in sys.argv[1:] if arg != "--skip"])

    try:
        incoming = normalize(json.load(sys.stdin))
    except Exception as err:
        print("error: could not parse entry: %s" % err, file=sys.stderr)
        return 1

    if not incoming["id"]:
        print("error: entry needs an id", file=sys.stderr)
        return 1

    # Read and write under one lock. More than one digest agent can be alive
    # at a time, and without this the later writer rewrites the index from a
    # copy it read before the earlier one's entry existed.
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(memlib.file_lock("index"))
        except (memlib.LockUnavailable, OSError):
            # Writing unsynchronised risks losing a concurrent entry.
            # Refusing to write loses this one for certain, and the
            # conversation stays in flight either way.
            print("upsert: index lock is held, writing unsynchronised",
                  file=sys.stderr)
        merge_into_index(incoming)

    # The entry is durable now, so the conversations behind it are done.
    # Resolving after the write means a crash in between costs a re-digest
    # rather than a lost transcript.
    memlib.resolve_inflight(incoming["conversations"])
    print("ok: %s" % incoming["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
