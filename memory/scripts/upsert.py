#!/usr/bin/env python3
"""Merge one memory entry into the index.

Reads a JSON object on stdin and merges it by `id`. List fields are unioned so
a topic accumulates conversations and keywords over time; `title`, `summary`
and `points` are replaced by the newest version.

Usage:
    echo '{"id": "topic", "title": "...", ...}' | python3 upsert.py
"""

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


def main():
    try:
        incoming = normalize(json.load(sys.stdin))
    except Exception as err:
        print("error: could not parse entry: %s" % err, file=sys.stderr)
        return 1

    if not incoming["id"]:
        print("error: entry needs an id", file=sys.stderr)
        return 1

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
    print("ok: %s" % incoming["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
