#!/usr/bin/env python3
"""Record whether an injected memory actually helped.

Retrieval logs what it injected. That says nothing about whether the entry was
any use, so without this the scoring can never improve and a persistently
useless entry keeps consuming an injection slot forever.

The judgement is made by the digest agent, which already reads the transcript
of the finished conversation and can see whether the injected material was
picked up or ignored. Doing it there costs nothing in the live conversation.

Usage:
    python3 feedback.py --used topic-slug
    python3 feedback.py --noise topic-slug
    python3 feedback.py --report
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402


def apply(entry_id, field):
    entries = memlib.read_index()
    for entry in entries:
        if entry.get("id") == entry_id:
            entry[field] = int(entry.get(field, 0) or 0) + 1
            memlib.write_index(entries)
            return entry
    return None


def report():
    entries = memlib.read_index()
    if not entries:
        print("index is empty")
        return
    rows = []
    for entry in entries:
        useful = int(entry.get("useful", 0) or 0)
        useless = int(entry.get("useless", 0) or 0)
        rows.append((
            entry.get("id", "?"),
            useful,
            useless,
            memlib.feedback_multiplier(entry),
            "SUPPRESSED" if memlib.is_suppressed(entry) else "",
        ))
    rows.sort(key=lambda r: r[3])
    print("%-30s %7s %7s %7s  %s" % ("id", "useful", "noise", "weight", ""))
    for row in rows:
        print("%-30s %7d %7d %7.2f  %s" % row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--used", metavar="ID",
                        help="the entry informed the answer")
    parser.add_argument("--noise", metavar="ID",
                        help="the entry was injected but irrelevant")
    parser.add_argument("--report", action="store_true",
                        help="show usefulness stats for every entry")
    args = parser.parse_args()

    if args.report:
        report()
        return 0

    if not args.used and not args.noise:
        parser.error("give --used, --noise, or --report")

    for entry_id, field in ((args.used, "useful"), (args.noise, "useless")):
        if not entry_id:
            continue
        entry = apply(entry_id, field)
        if entry is None:
            print("no entry with id %s" % entry_id, file=sys.stderr)
            return 1
        print("%s: useful=%d useless=%d weight=%.2f%s" % (
            entry_id,
            int(entry.get("useful", 0) or 0),
            int(entry.get("useless", 0) or 0),
            memlib.feedback_multiplier(entry),
            " SUPPRESSED" if memlib.is_suppressed(entry) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
