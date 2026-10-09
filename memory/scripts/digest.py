#!/usr/bin/env python3
"""Digest queued conversations into the memory index.

Run on a schedule by the memory-digest sidecar. Picks up conversations the
Stop hook marked dirty, drops the ones not worth summarizing, and hands the
survivors to a single batched agent conversation. One conversation per run,
not one per transcript, to keep the conversation list readable.

The agent it spawns writes entries through upsert.py rather than editing the
index directly.

Dispatched conversations move to an in-flight file rather than being deleted.
Starting an agent is not the same as that agent finishing: `agentapi
new-conversation` returns as soon as the conversation exists. A record leaves
the in-flight file only when upsert.py confirms it, so an agent that dies,
is cancelled, or runs out of context does not take a batch of transcripts
with it.

Usage:
    python3 digest.py                 select a batch and spawn an agent
    python3 digest.py --dry-run       show what would be selected
    python3 digest.py --prepare       select a batch for an agent you start
    python3 digest.py --attach <id>   name the agent that took it

--prepare and --attach exist because `agentapi new-conversation` fails in
some builds. They do the same bookkeeping as a normal run either side of the
spawn, leaving the spawn to the caller.
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
# How long a digest agent has before anyone asks whether it is still alive.
# Nothing is requeued inside this window, which also covers the gap between
# spawning an agent and its transcript appearing on disk.
INFLIGHT_GRACE = 60 * 60
# Past the grace period, a batch is only taken away from an agent that has
# written nothing for this long. A batch can be twenty transcripts, and an
# agent working steadily through them must not have them handed to a second
# agent just for being slow.
AGENT_IDLE = 20 * 60
# Ceiling on the above. An agent that looks busy forever is wedged, and its
# batch should not be hostage to it.
INFLIGHT_MAX = 6 * 60 * 60
# An agent that skipped a transcript without acknowledging looks exactly like
# one that died holding it, so the ambiguity is bounded rather than guessed:
# retry a few times, then park the record for a human.
MAX_ATTEMPTS = 3

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
    queue = {}
    for record in memlib.read_jsonl(memlib.DIRTY_PATH):
        cid = record.get("id")
        if not cid:
            continue
        if cid not in queue or record.get("ts", 0) > queue[cid].get("ts", 0):
            queue[cid] = record
    return queue


def save_queue(queue):
    memlib.write_jsonl(memlib.DIRTY_PATH, list(queue.values()))


# Bookkeeping about one particular handover, dropped when the record is
# requeued because the next handover will be to a different agent. The
# attempt count is deliberately not in here: it has to survive the round trip
# through the queue or the retry cap never bites.
_DISPATCH_FIELDS = ("dispatched", "digest_conversation")


def agent_is_working(record, now):
    """Whether the agent holding this record is still making progress.

    A digest agent appends to its own transcript as it reads each one, so the
    mtime of that file separates an agent that is slow from one that is gone.
    Without this check a batch of twenty transcripts would be taken away from
    a perfectly healthy agent and handed to a second one, and the attempt
    count would climb until the records were parked for no reason.
    """
    spawned = record.get("digest_conversation")
    if not spawned:
        return False
    try:
        idle = now - os.path.getmtime(memlib.transcript_path_for(spawned))
    except OSError:
        return False
    return 0 <= idle < AGENT_IDLE


def park_undigested(records, reason):
    """Set aside work that repeated attempts could not digest.

    Deleting it here would be the original bug in a new place, so the records
    are appended to undigested.jsonl instead. Nothing reads that file; it
    exists so the loss is visible and the transcripts can be recovered by
    hand.
    """
    for record in records:
        parked = dict(record)
        parked["parked"] = memlib.now_iso()
        parked["reason"] = reason
        memlib.append_jsonl(memlib.UNDIGESTED_PATH, parked)
        print("digest: parked %s in %s after %d attempts (%s)"
              % (record.get("id"), memlib.UNDIGESTED_PATH,
                 record.get("attempts", 0), reason), file=sys.stderr)


def recover_inflight(queue, now):
    """Requeue work whose agent never confirmed it. Returns what stays in flight.

    upsert.py clears a record as soon as an entry naming its conversation
    reaches the index, or when the agent acknowledges a skip. Anything left
    here belongs to an agent that died, was cancelled, or silently dropped the
    transcript, so it goes back on the queue for a later run.
    """
    records = memlib.read_inflight()
    if not records:
        return []

    # Belt and braces: an entry naming the conversation means it was digested
    # even if the resolution call itself was lost to a lock timeout.
    digested = set()
    for entry in memlib.read_index():
        digested.update(entry.get("conversations", []) or [])

    kept, exhausted = [], []
    for record in records:
        cid = record.get("id")
        if not cid or cid in digested:
            continue

        # A handover stamped in the future means the clock moved backwards.
        # Treating that as fresh would pin the record in flight for good, so
        # only a positive age counts as time spent waiting.
        age = now - record.get("dispatched", 0)
        within_ceiling = 0 <= age < INFLIGHT_MAX
        if within_ceiling and (age < INFLIGHT_GRACE
                               or agent_is_working(record, now)):
            kept.append(record)
            continue

        if record.get("attempts", 0) >= MAX_ATTEMPTS:
            exhausted.append(record)
            continue

        requeued = {k: v for k, v in record.items() if k not in _DISPATCH_FIELDS}
        existing = queue.get(cid)
        # A Stop hook append that arrived while the agent was working is the
        # fresher record, but the attempt count has to survive either way or
        # the retry cap never bites.
        attempts = max(record.get("attempts", 0),
                       (existing or {}).get("attempts", 0))
        if existing is None or requeued.get("ts", 0) > existing.get("ts", 0):
            queue[cid] = requeued
        queue[cid]["attempts"] = attempts

    park_undigested(exhausted, "no agent confirmed it")
    return kept


def dispatch(selected, digest_conversation, now):
    """Move the dispatched records from the queue to the in-flight file.

    Re-reads the queue under the lock and only removes records the agent was
    actually given, matched on timestamp, so a Stop hook append that landed
    while the agent was starting is preserved rather than overwritten.
    """
    with memlib.file_lock("queues"):
        queue = load_queue()
        inflight = {r.get("id"): r for r in memlib.read_inflight()}
        for cid, record in selected:
            current = queue.get(cid)
            if current is not None and current.get("ts", 0) <= record.get("ts", 0):
                queue.pop(cid)
            tracked = {k: v for k, v in record.items() if k != "attempts"}
            tracked["dispatched"] = now
            tracked["digest_conversation"] = digest_conversation
            tracked["attempts"] = record.get("attempts", 0) + 1
            inflight[cid] = tracked
        save_queue(queue)
        memlib.write_inflight(list(inflight.values()))


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
   conversation. Skip it if it is only chit-chat, a question answered from a
   file already open, a task that failed and was abandoned, or a duplicate of
   something already in the index. Record every skip:

   python3 {scripts}/upsert.py --skip <conversation-id>

   This is not optional. A transcript you leave unacknowledged is
   indistinguishable from one you were given and never got to, so it will be
   handed to another agent later and the work will be done twice.
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


def drop_stale(stale):
    """Forget queue records that can never become eligible.

    Matched on timestamp so a fresh Stop hook append for the same conversation
    survives and gets re-evaluated on the next run.
    """
    if not stale:
        return
    try:
        with memlib.file_lock("queues"):
            queue = load_queue()
            for cid, record in stale:
                current = queue.get(cid)
                if current is not None and current.get("ts", 0) <= record.get("ts", 0):
                    queue.pop(cid)
            save_queue(queue)
    except memlib.LockUnavailable:
        # Nothing is lost by leaving them; the next run sweeps again.
        print("digest: could not sweep stale records, lock is held",
              file=sys.stderr)


def report_dry_run(queue, selected, stale, now):
    print("digest: %d queued, %d eligible, %d stale, %d in flight"
          % (len(queue), len(selected), len(stale), len(memlib.read_inflight())))
    for cid, rec in queue.items():
        state = "ELIGIBLE" if (cid, rec) in selected else "skip"
        age = int(now - rec.get("ts", 0))
        attempts = rec.get("attempts", 0)
        suffix = "  attempts=%d" % attempts if attempts else ""
        print("  %-9s %s  age=%ds%s" % (state, cid, age, suffix))
    for record in memlib.read_inflight():
        print("  INFLIGHT  %s  dispatched=%ds ago  attempts=%d"
              % (record.get("id"), int(now - record.get("dispatched", 0)),
                 record.get("attempts", 0)))
    if selected:
        print("--- prompt that would be sent ---")
        print(build_prompt(selected[:MAX_PER_RUN]))


def prompt_path():
    # Read through memlib each time so tests that redirect MEMORY_DIR do not
    # write into the real one.
    return os.path.join(memlib.MEMORY_DIR, "digest_prompt.txt")


# Stands in for the digest conversation between --prepare and --attach. The
# id of a subagent is only known once it has started, and the handover has to
# be recorded before it is allowed to touch the queue.
PENDING = "pending-subagent"


def hand_off(selected, now):
    """Write the batch out for an agent this process cannot start itself.

    `agentapi new-conversation` is broken in some builds, so the digest cannot
    spawn anything. The caller passes the prompt to a subagent instead, which
    finishes the round in exactly the same way, through upsert.py.
    """
    path = prompt_path()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_prompt(selected))

    try:
        dispatch(selected, PENDING, now)
    except memlib.LockUnavailable:
        print("digest: could not record handover, batch stays queued",
              file=sys.stderr)
        return 1

    drop_injections([cid for cid, _ in selected])
    print("digest: %d conversation(s) ready for an agent" % len(selected))
    for cid, _ in selected:
        print("  %s" % cid)
    print("prompt: %s" % path)
    print("then:   python3 %s --attach <conversation-id>"
          % os.path.abspath(__file__))
    return 0


def attach(conversation_id):
    """Name the agent that took the batch prepared by --prepare.

    Until this runs, recover_inflight cannot tell a working agent from a dead
    one, because agent_is_working looks at the transcript of the conversation
    named in the record.
    """
    if not conversation_id:
        print("digest: --attach needs a conversation id", file=sys.stderr)
        return 2
    try:
        with memlib.file_lock("queues"):
            records = memlib.read_inflight()
            pending = [r for r in records
                       if r.get("digest_conversation") == PENDING]
            for record in pending:
                record["digest_conversation"] = conversation_id
                # Restart the clock: the grace period should run from when a
                # real agent picked the work up.
                record["dispatched"] = time.time()
            if pending:
                memlib.write_inflight(records)
    except memlib.LockUnavailable:
        print("digest: queue lock is held, try again", file=sys.stderr)
        return 1

    if not pending:
        print("digest: nothing was waiting to be attached", file=sys.stderr)
        return 1

    # Otherwise the next run would summarize the digest agent's own
    # conversation and requeue forever.
    add_exclusion(conversation_id)
    print("digest: attached %d record(s) to %s" % (len(pending), conversation_id))
    return 0


def main():
    if "--dry-run" in sys.argv:
        return run(dry_run=True)

    if "--attach" in sys.argv:
        rest = [arg for arg in sys.argv[1:] if arg != "--attach"]
        return attach(rest[0] if rest else "")

    prepare = "--prepare" in sys.argv

    # Held for the whole run, unlike the queue lock, which is only taken
    # across a rewrite. Without it two runs each read the same queue before
    # either dispatched and hand the same batch to two agents.
    try:
        with memlib.file_lock("digest", timeout=0.0):
            return run(dry_run=False, prepare=prepare)
    except memlib.LockUnavailable:
        # Not an error. The other run is doing the work.
        print("digest: another run is in progress, skipping", file=sys.stderr)
        return 0


def run(dry_run, prepare=False):
    memlib.ensure_dirs()
    now = time.time()

    if not dry_run:
        fold_access_log()
        # Recovery rewrites both queues, so it holds the lock. The agent spawn
        # further down deliberately does not: it can take two minutes, and the
        # Stop hook must not block behind it.
        try:
            with memlib.file_lock("queues"):
                queue = load_queue()
                memlib.write_inflight(recover_inflight(queue, now))
                save_queue(queue)
        except memlib.LockUnavailable:
            print("digest: queue lock is held, skipping this run", file=sys.stderr)
            return 1
    else:
        # Recovery parks records and rewrites both queues, so a dry run must
        # not call it. report_dry_run shows the in-flight file as it stands.
        queue = load_queue()

    if not queue:
        if dry_run:
            print("digest: queue empty, %d in flight"
                  % len(memlib.read_inflight()))
        return 0

    exclusions = read_exclusions()
    selected = [(cid, rec) for cid, rec in queue.items()
                if eligible(cid, rec, exclusions, now)]
    # Conversations that will never become eligible should not sit in the
    # queue forever.
    stale = [(cid, rec) for cid, rec in queue.items()
             if now - rec.get("ts", 0) > memlib.IDLE_SECONDS
             and (cid, rec) not in selected]

    if dry_run:
        report_dry_run(queue, selected, stale, now)
        return 0

    if not selected:
        drop_stale(stale)
        return 0

    selected.sort(key=lambda pair: pair[1].get("ts", 0), reverse=True)
    selected = selected[:MAX_PER_RUN]

    if prepare:
        code = hand_off(selected, now)
        if code == 0:
            drop_stale(stale)
        return code

    try:
        result = subprocess.run(
            [AGENTAPI, "new-conversation", "--model=flash",
             "--title=Memory digest", build_prompt(selected)],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as err:
        print("digest: could not start agent: %s" % err, file=sys.stderr)
        return 1

    if result.returncode != 0:
        # No agent, so nothing was handed over. Leaving the queue untouched
        # means the next run retries instead of dropping the batch.
        print("digest: agentapi exited %d: %s"
              % (result.returncode, (result.stderr or "").strip()[:200]),
              file=sys.stderr)
        return 1

    spawned = _UUID.findall((result.stdout or "") + (result.stderr or ""))
    # Record the spawned conversation so the next run does not digest it.
    for match in spawned:
        add_exclusion(match)

    try:
        dispatch(selected, spawned[0] if spawned else "", now)
    except memlib.LockUnavailable:
        # The agent is running with this batch, but the handover could not be
        # recorded. Leaving the records dirty risks a duplicate digest, which
        # merges harmlessly; dropping them would lose the transcripts.
        print("digest: could not record handover, batch stays queued",
              file=sys.stderr)
        return 1

    drop_injections([cid for cid, _ in selected])
    drop_stale(stale)

    print("digest: dispatched %d conversation(s) to %s"
          % (len(selected), spawned[0] if spawned else "an agent"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
