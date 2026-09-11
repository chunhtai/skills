#!/usr/bin/env python3
"""Digest queued conversations into the memory index.

Run on a schedule by the memory-digest sidecar. Picks up conversations the
Stop hook marked dirty, drops the ones not worth summarizing, and hands the
survivors to a single batched agent conversation. One conversation per run,
not one per transcript, to keep the conversation list readable.

The agent it spawns writes entries through upsert.py rather than editing the
index directly.
"""

import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memlib  # noqa: E402

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
EXCLUDE_PATH = os.path.join(memlib.MEMORY_DIR, "exclude.txt")
AGENTAPI = memlib.agentapi_path()

# Conversations shorter than this rarely contain reusable knowledge.
MIN_USER_INPUTS = 2
# Bound the work a single run can queue up.
MAX_PER_RUN = 20

_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def read_exclusions():
    """Conversations this system created itself.

    Without this the digest would summarize its own output and requeue
    forever.
    """
    if not os.path.exists(EXCLUDE_PATH):
        return set()
    with open(EXCLUDE_PATH, "r", encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def add_exclusion(conversation_id):
    memlib.ensure_dirs()
    with open(EXCLUDE_PATH, "a", encoding="utf-8") as fh:
        fh.write(conversation_id + "\n")


def load_queue():
    """Collapse the dirty log to the newest record per conversation."""
    if not os.path.exists(memlib.DIRTY_PATH):
        return {}
    queue = {}
    with open(memlib.DIRTY_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = record.get("id")
            if not cid:
                continue
            if cid not in queue or record.get("ts", 0) > queue[cid].get("ts", 0):
                queue[cid] = record
    return queue


def save_queue(queue):
    memlib.ensure_dirs()
    tmp = memlib.DIRTY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for record in queue.values():
            fh.write(json.dumps(record) + "\n")
    os.replace(tmp, memlib.DIRTY_PATH)


def fold_access_log():
    """Apply retrieval hits recorded by the hook to the index.

    The hook cannot write the index itself without racing this script.
    """
    if not os.path.exists(memlib.ACCESS_LOG):
        return
    hits = {}
    try:
        with open(memlib.ACCESS_LOG, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split("\t")
                if len(parts) != 2:
                    continue
                hits[parts[1]] = max(hits.get(parts[1], 0.0), float(parts[0]))
    except (OSError, ValueError):
        return
    if not hits:
        return
    entries = memlib.read_index()
    changed = False
    for entry in entries:
        stamp = hits.get(entry.get("id"))
        if stamp:
            entry["accessed"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(stamp))
            changed = True
    if changed:
        memlib.write_index(entries)
    os.remove(memlib.ACCESS_LOG)


def eligible(cid, record, exclusions, now):
    if cid in exclusions:
        return False
    if now - record.get("ts", 0) < memlib.IDLE_SECONDS:
        return False  # still active
    transcript = record.get("transcript") or memlib.transcript_path_for(cid)
    if not os.path.exists(transcript):
        return False
    if memlib.is_subagent(cid):
        return False
    if memlib.count_user_inputs(transcript) < MIN_USER_INPUTS:
        return False
    return True


PROMPT = """You are digesting finished agent conversations into a keyword-searchable memory index. Work autonomously and do not ask questions.

For each transcript listed below:

1. Read it.
2. Decide whether it contains knowledge that would help in a future, different
   conversation. Say SKIP and move on if it is only chit-chat, a question
   answered from a file already open, a task that failed and was abandoned, or
   a duplicate of something already in the index.
3. If it is worth keeping, build an entry and merge it:

   echo '<json>' | python3 {scripts}/upsert.py

Entry fields:

  id           short kebab-case topic slug, reuse an existing id to merge into
               that topic instead of creating a near-duplicate
  title        short human title
  summary      2 to 3 sentences on what was established
  points       bullet list of concrete conclusions, decisions, or gotchas
  keywords     THE MOST IMPORTANT FIELD. Literal strings a future question
               would contain: identifiers, symbol names, error text, flag
               names, package names, file basenames, product names. Include
               odd tokens verbatim. Retrieval is keyword matching, so prose
               here is wasted and a missing identifier means the entry is
               unfindable.
  aliases      The same topic in the words someone would use who does not
               know the identifier. If the entry is about
               `RenderFlex overflowed`, aliases are "yellow black stripes",
               "row too wide", "layout overflow". This is what makes a
               paraphrased question find the entry, so spend real effort
               here.
  workspaces   absolute workspace roots the work happened in
  files        absolute paths of files read or changed that mattered
  conversations  the conversation id, so the full transcript can be recovered
  corrections  where the user corrected the agent. Each item:
               {{"did": "what the agent did", "should": "what it should have
               done", "kind": "preference" or "procedure"}}
               Use "preference" for tone, formatting, verbosity, or style.
               Use "procedure" for a concrete repeatable technique, command
               sequence, or workflow rule.
               Only record real user corrections. An agent noticing its own
               mistake is not a correction. Leave the list empty if there
               were none.

Read the current index first so you reuse topic ids:

  cat {index}

Transcripts to process:

{transcripts}

{grading}
When finished, reply with one line per transcript: the id you merged into, or
SKIP and a short reason.
"""

GRADING = """
Grade past retrievals.

Memory was injected into some of these conversations before the agent
answered. For each entry listed below, read the transcript and judge whether
that injected memory actually informed the answer, then record it:

  python3 {scripts}/feedback.py --used <entry-id>
  python3 {scripts}/feedback.py --noise <entry-id>

Use --used only when the answer drew on the entry. Injected but ignored, or
injected and irrelevant to what was asked, is --noise. Guessing generously
here defeats the purpose: the counts feed back into ranking, and an entry
marked useful when it was not will keep crowding out better matches.

Injections to grade:

{injections}
"""



def load_injections(conversation_ids):
    """What was injected into each conversation being digested."""
    if not os.path.exists(memlib.INJECTIONS_PATH):
        return {}
    wanted = set(conversation_ids)
    found = {}
    with open(memlib.INJECTIONS_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = record.get("conversation")
            if cid in wanted:
                for entry_id in record.get("entries", []):
                    found.setdefault(cid, set()).add(entry_id)
    return found


def drop_injections(conversation_ids):
    """Forget graded injections so they are not graded twice."""
    if not os.path.exists(memlib.INJECTIONS_PATH):
        return
    done = set(conversation_ids)
    kept = []
    with open(memlib.INJECTIONS_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("conversation") not in done:
                kept.append(line.rstrip("\n"))
    tmp = memlib.INJECTIONS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
    os.replace(tmp, memlib.INJECTIONS_PATH)


def build_prompt(selected):
    lines = []
    for cid, record in selected:
        transcript = record.get("transcript") or memlib.transcript_path_for(cid)
        lines.append("- conversation %s\n  transcript: %s\n  workspace: %s"
                     % (cid, transcript, record.get("workspace", "")))

    injections = load_injections([cid for cid, _ in selected])
    if injections:
        injection_lines = []
        for cid, entry_ids in injections.items():
            injection_lines.append("- conversation %s saw: %s"
                                   % (cid, ", ".join(sorted(entry_ids))))
        grading = GRADING.format(scripts=SCRIPTS_DIR,
                                 injections="\n".join(injection_lines))
    else:
        grading = ""

    return PROMPT.format(scripts=SCRIPTS_DIR, index=memlib.INDEX_PATH,
                         transcripts="\n".join(lines), grading=grading)


def main():
    dry_run = "--dry-run" in sys.argv
    memlib.ensure_dirs()
    if not dry_run:
        fold_access_log()

    queue = load_queue()
    if not queue:
        if dry_run:
            print("digest: queue empty")
        return 0

    exclusions = read_exclusions()
    now = time.time()
    selected = [(cid, rec) for cid, rec in queue.items()
                if eligible(cid, rec, exclusions, now)]
    # Conversations that will never become eligible should not sit in the
    # queue forever.
    stale = [cid for cid, rec in queue.items()
             if now - rec.get("ts", 0) > memlib.IDLE_SECONDS
             and (cid, rec) not in selected]

    if dry_run:
        print("digest: %d queued, %d eligible, %d stale"
              % (len(queue), len(selected), len(stale)))
        for cid, rec in queue.items():
            state = "ELIGIBLE" if (cid, rec) in selected else "skip"
            age = int(now - rec.get("ts", 0))
            print("  %-9s %s  age=%ds" % (state, cid, age))
        if selected:
            print("--- prompt that would be sent ---")
            print(build_prompt(selected[:MAX_PER_RUN]))
        return 0

    if not selected:
        for cid in stale:
            queue.pop(cid, None)
        save_queue(queue)
        return 0

    selected.sort(key=lambda pair: pair[1].get("ts", 0), reverse=True)
    selected = selected[:MAX_PER_RUN]

    try:
        result = subprocess.run(
            [AGENTAPI, "new-conversation", "--model=flash",
             "--title=Memory digest", build_prompt(selected)],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as err:
        print("digest: could not start agent: %s" % err, file=sys.stderr)
        return 1

    # Record the spawned conversation so the next run does not digest it.
    for match in _UUID.findall((result.stdout or "") + (result.stderr or "")):
        add_exclusion(match)

    drop_injections([cid for cid, _ in selected])

    for cid, _ in selected:
        queue.pop(cid, None)
    for cid in stale:
        queue.pop(cid, None)
    save_queue(queue)

    print("digest: queued %d conversation(s)" % len(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
