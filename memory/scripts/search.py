#!/usr/bin/env python3
"""Search the conversation memory index.

Used by the search-conversation-memory skill for deliberate lookups, which are
broader than the automatic hook: lower score threshold, more results, and the
transcript path for each hit so the full history can be read.

Usage:
    python3 search.py "focus traversal semantics"
    python3 search.py --limit 10 --min-score 1 "deprecation version"
    python3 search.py --list
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


def print_entry(score, entry):
    print("## %s  (id: %s, score %.1f)" % (
        entry.get("title", "?"), entry.get("id", "?"), score))
    print("modified: %s" % (entry.get("modified", "")[:10] or "unknown"))
    if entry.get("summary"):
        print(entry["summary"])
    for point in entry.get("points", []) or []:
        print("  * %s" % point)
    keywords = entry.get("keywords", []) or []
    if keywords:
        print("keywords: %s" % ", ".join(keywords[:20]))
    files = entry.get("files", []) or []
    if files:
        print("files:")
        for path in files[:8]:
            print("  %s" % path)
    convs = entry.get("conversations", []) or []
    if convs:
        print("transcripts:")
        for cid in convs[:5]:
            print("  %s  ->  %s" % (cid, memlib.transcript_path_for(cid)))
    corrections = entry.get("corrections", []) or []
    for correction in corrections:
        print("correction (%s): instead of %s, %s" % (
            correction.get("kind", "?"), correction.get("did", ""),
            correction.get("should", "")))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="*", help="search terms")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=1.0)
    parser.add_argument("--workspace", default=None,
                        help="restrict scoring to a workspace root")
    parser.add_argument("--list", action="store_true",
                        help="list every entry, newest first")
    parser.add_argument("--json", action="store_true",
                        help="emit raw JSON instead of text")
    args = parser.parse_args()

    if args.list:
        entries = memlib.read_index()
        entries.sort(key=lambda e: e.get("modified", ""), reverse=True)
        if args.json:
            print(json.dumps(entries, indent=2))
            return 0
        if not entries:
            print("index is empty")
            return 0
        for entry in entries:
            print("%-28s %s  (%s)" % (
                entry.get("id", "?"),
                entry.get("title", ""),
                (entry.get("modified", "") or "")[:10]))
        return 0

    query = " ".join(args.query).strip()
    if not query:
        parser.error("give a query or use --list")

    hits = memlib.search(query, workspace=args.workspace, limit=args.limit,
                         min_score=args.min_score)
    if args.json:
        print(json.dumps([{"score": s, "entry": e} for s, e in hits], indent=2))
        return 0

    if not hits:
        print("no matches for: %s" % query)
        print("index holds %d entries; try --min-score 0.5 or --list"
              % len(memlib.read_index()))
        return 0

    memlib.log_access([entry.get("id", "") for _, entry in hits])
    for score, entry in hits:
        print_entry(score, entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
