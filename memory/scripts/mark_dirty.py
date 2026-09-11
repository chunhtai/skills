#!/usr/bin/env python3
"""Stop hook: mark the current conversation as needing a digest.

Runs after every turn, inside the agent loop, so it must stay cheap. It does
no parsing and no model calls, only an append. The digest sidecar decides
later whether the conversation is worth summarizing.

Always prints valid JSON and exits 0. A crash here would stall every turn.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    try:
        import memlib

        conversation_id = payload.get("conversationId", "")
        if conversation_id:
            workspaces = payload.get("workspacePaths") or []
            memlib.append_jsonl(memlib.DIRTY_PATH, {
                "id": conversation_id,
                "ts": time.time(),
                "workspace": workspaces[0] if workspaces else "",
                "transcript": payload.get("transcriptPath", ""),
                "artifacts": payload.get("artifactDirectoryPath", ""),
            })
    except Exception:
        # Never block the agent because bookkeeping failed.
        pass

    # Any decision other than "continue" lets the agent stop normally.
    print("{}")


if __name__ == "__main__":
    main()
