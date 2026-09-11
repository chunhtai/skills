#!/usr/bin/env python3
"""PreInvocation hook: inject relevant past work before the model runs.

Fires once per new user message. Gating is by the content of the latest user
input rather than by `invocationNum`, so it survives whatever that counter
means and, more importantly, keeps working when the topic shifts mid
conversation. Gating on the first invocation only would search the opening
line and nothing else, which is wrong for any conversation that wanders.

Summaries only, never transcripts. The conversation ids are included so the
agent can pull the full history itself if it turns out to matter.

Always prints valid JSON and exits 0.
"""

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEADER = (
    "Possibly relevant past work from your memory index. This is retrieved by "
    "keyword match and may be stale or wrong. Verify against the current code "
    "before relying on any of it; prefer reading the source over trusting a "
    "summary.\n"
)


def build_message(hits):
    lines = [HEADER]
    for score, entry in hits:
        modified = (entry.get("modified") or "")[:10]
        summary = entry.get("summary", "").strip()
        if len(summary) > 240:
            summary = summary[:237].rstrip() + "..."
        lines.append("* [%s] %s: %s" % (modified or "undated",
                                        entry.get("title", entry.get("id", "?")),
                                        summary))
        points = entry.get("points") or []
        for point in points[:3]:
            lines.append("    - %s" % point)
        convs = entry.get("conversations") or []
        if convs:
            lines.append("    conversations: %s" % ", ".join(convs[:3]))
    return "\n".join(lines)


def already_handled(memlib, conversation_id, query):
    """True when this exact user message was already searched.

    A conversation makes many model calls per user turn, one per tool step.
    Without this the hook would re-inject the same block on every step.
    """
    if not conversation_id:
        return False
    marker_dir = os.path.join(memlib.MEMORY_DIR, "seen")
    marker = os.path.join(marker_dir, conversation_id)
    digest = hashlib.sha256(query.encode("utf-8", "replace")).hexdigest()
    try:
        with open(marker, "r", encoding="utf-8") as fh:
            if fh.read().strip() == digest:
                return True
    except OSError:
        pass
    try:
        os.makedirs(marker_dir, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(digest)
    except OSError:
        pass
    return False


def log(memlib, conversation_id, invocation, query, hits, injected):
    """Record every decision so retrieval quality can be judged later.

    Without this there is no way to tell the difference between "memory never
    matches" and "memory matches but is useless".
    """
    try:
        path = os.path.join(memlib.MEMORY_DIR, "retrieve.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\tinv=%s\thits=%d\tinjected=%s\tq=%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                (conversation_id or "?")[:8],
                invocation,
                len(hits),
                injected,
                " ".join(query.split())[:80]))
    except OSError:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return

    try:
        import memlib

        transcript = payload.get("transcriptPath", "")
        query = memlib.last_user_input(transcript)
        if not query:
            print("{}")
            return

        conversation_id = payload.get("conversationId", "")
        invocation = payload.get("invocationNum", "?")

        if already_handled(memlib, conversation_id, query):
            print("{}")
            return

        workspaces = payload.get("workspacePaths") or []
        workspace = workspaces[0] if workspaces else None
        hits = memlib.search(
            query,
            workspace=workspace,
            exclude_conversation=conversation_id,
        )
        log(memlib, conversation_id, invocation, query, hits, bool(hits))
        if not hits:
            print("{}")
            return

        ids = [entry.get("id", "") for _, entry in hits]
        memlib.log_access(ids)
        # Ledger for the digest to grade later.
        memlib.append_jsonl(memlib.INJECTIONS_PATH, {
            "conversation": conversation_id,
            "ts": time.time(),
            "entries": ids,
            "query": " ".join(query.split())[:200],
        })
        print(json.dumps({
            "injectSteps": [{"ephemeralMessage": build_message(hits)}]
        }))
    except Exception:
        print("{}")


if __name__ == "__main__":
    main()
