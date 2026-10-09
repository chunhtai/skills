#!/usr/bin/env python3
"""Tests for the digest queue handover.

The property under test is that a conversation the Stop hook recorded is
never dropped until something confirms it was digested. Starting an agent is
not confirmation, and neither is a rewrite of the queue that raced an append.

The harness reads the queue files directly and tolerates a missing in-flight
file, so the behavioural tests still fail as assertions when run against a
version of the scripts that has no in-flight tracking at all.

Run with:
    python3 -m unittest discover -s memory/tests
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)

import digest  # noqa: E402
import memlib  # noqa: E402
import upsert  # noqa: E402

SPAWNED = "aaaaaaaa-0000-4000-8000-000000000001"
CONV_A = "11111111-1111-4111-8111-111111111111"
CONV_B = "22222222-2222-4222-8222-222222222222"

GRACE = getattr(digest, "INFLIGHT_GRACE", 3600)
AGENT_IDLE = getattr(digest, "AGENT_IDLE", 1200)
MAX_ATTEMPTS = getattr(digest, "MAX_ATTEMPTS", 3)

_REDIRECTED = ("MEMORY_DIR", "INDEX_PATH", "DIRTY_PATH", "INFLIGHT_PATH",
               "UNDIGESTED_PATH", "ACCESS_LOG", "INJECTIONS_PATH",
               "PROPOSALS_DIR", "GENERATED_SKILLS_DIR", "APP_DATA_DIR",
               "CONVERSATIONS_DIR")


def read_jsonl(path):
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


class QueueTestCase(unittest.TestCase):
    """Points the memory system at a scratch directory for each test."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="memtest-")
        self.saved = {name: getattr(memlib, name, None) for name in _REDIRECTED}
        self.saved_exclude = digest.EXCLUDE_PATH

        memory = os.path.join(self.root, "memory")
        memlib.MEMORY_DIR = memory
        memlib.INDEX_PATH = os.path.join(memory, "index.jsonl")
        memlib.DIRTY_PATH = os.path.join(memory, "dirty.jsonl")
        memlib.INFLIGHT_PATH = os.path.join(memory, "inflight.jsonl")
        memlib.UNDIGESTED_PATH = os.path.join(memory, "undigested.jsonl")
        memlib.ACCESS_LOG = os.path.join(memory, "access.log")
        memlib.INJECTIONS_PATH = os.path.join(memory, "injections.jsonl")
        memlib.PROPOSALS_DIR = os.path.join(memory, "proposals")
        memlib.GENERATED_SKILLS_DIR = os.path.join(self.root, "skills")
        # transcript_path_for and is_subagent read these, so a test that fakes
        # an agent transcript must not reach into the real editor data.
        memlib.APP_DATA_DIR = os.path.join(self.root, "app")
        memlib.CONVERSATIONS_DIR = os.path.join(self.root, "app", "conversations")
        digest.EXCLUDE_PATH = os.path.join(memory, "exclude.txt")
        memlib.ensure_dirs()

        self.inflight_path = memlib.INFLIGHT_PATH
        self.undigested_path = memlib.UNDIGESTED_PATH

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(memlib, name, value)
        digest.EXCLUDE_PATH = self.saved_exclude
        shutil.rmtree(self.root, ignore_errors=True)

    # Helpers.

    def queue_conversation(self, conversation_id, age=3600, user_inputs=2):
        """Write a dirty record backed by a transcript that passes eligibility."""
        transcript = os.path.join(self.root, conversation_id + ".jsonl")
        with open(transcript, "w", encoding="utf-8") as fh:
            for i in range(user_inputs):
                fh.write(json.dumps({"type": "USER_INPUT", "content": "q%d" % i})
                         + "\n")
        record = {
            "id": conversation_id,
            "ts": time.time() - age,
            "workspace": "/tmp/workspace",
            "transcript": transcript,
            "artifacts": "",
        }
        memlib.append_jsonl(memlib.DIRTY_PATH, record)
        return record

    def run_digest(self, returncode=0, side_effect=None):
        """Run a digest with the agent spawn stubbed out."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout="started " + SPAWNED,
            stderr="")

        def fake_run(*args, **kwargs):
            if side_effect is not None:
                side_effect()
            return completed

        with mock.patch.object(digest.subprocess, "run", fake_run):
            return digest.main()

    def dirty_ids(self):
        return sorted(r["id"] for r in read_jsonl(memlib.DIRTY_PATH))

    def inflight(self):
        return {r["id"]: r for r in read_jsonl(self.inflight_path)}

    def tracked_ids(self):
        """Every conversation the system still knows it owes work on."""
        return sorted(set(self.dirty_ids()) | set(self.inflight())
                      | set(self.undigested_ids()))

    def undigested_ids(self):
        return sorted(r["id"] for r in read_jsonl(self.undigested_path))

    def write_agent_transcript(self, conversation_id=SPAWNED, idle=0):
        """Fake the digest agent's own transcript, last written `idle` ago."""
        path = memlib.transcript_path_for(conversation_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        stamp = time.time() - idle
        os.utime(path, (stamp, stamp))
        return path

    def age_inflight(self, seconds):
        """Backdate the handover so the grace period has expired."""
        records = read_jsonl(self.inflight_path)
        for record in records:
            record["dispatched"] = record.get("dispatched", 0) - seconds
        if records:
            write_jsonl(self.inflight_path, records)

    def write_entry(self, conversations):
        memlib.write_index([{
            "id": "topic",
            "title": "t",
            "conversations": list(conversations),
        }])


class DispatchTest(QueueTestCase):

    def test_dispatched_conversation_is_still_tracked(self):
        """Starting an agent is not proof the agent finished."""
        self.queue_conversation(CONV_A)

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.tracked_ids(), [CONV_A])
        self.assertEqual(self.inflight()[CONV_A]["attempts"], 1)
        self.assertEqual(self.inflight()[CONV_A]["digest_conversation"], SPAWNED)

    def test_spawn_failure_leaves_queue_intact(self):
        """A non-zero agentapi exit means no agent, so nothing was handed over."""
        self.queue_conversation(CONV_A)

        self.assertEqual(self.run_digest(returncode=1), 1)

        self.assertEqual(self.dirty_ids(), [CONV_A])
        self.assertEqual(self.inflight(), {})

    def test_append_during_spawn_is_not_overwritten(self):
        """The Stop hook can fire while the agent is starting."""
        self.queue_conversation(CONV_A)

        self.assertEqual(
            self.run_digest(side_effect=lambda: self.queue_conversation(CONV_B)),
            0)

        self.assertEqual(self.dirty_ids(), [CONV_B])


class HandOffTest(QueueTestCase):
    """--prepare and --attach, for when agentapi cannot spawn the agent."""

    def prepare(self):
        # No spawn should happen at all, so any call to subprocess is a bug.
        with mock.patch.object(digest.subprocess, "run",
                               side_effect=AssertionError("spawned an agent")):
            with mock.patch.object(sys, "argv", ["digest.py", "--prepare"]):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    code = digest.main()
        return code, out.getvalue()

    def attach(self, conversation_id):
        argv = ["digest.py", "--attach", conversation_id]
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    return digest.main()

    def test_prepare_tracks_the_batch_and_writes_the_prompt(self):
        self.queue_conversation(CONV_A)

        code, out = self.prepare()

        self.assertEqual(code, 0)
        self.assertEqual(self.tracked_ids(), [CONV_A])
        self.assertEqual(self.inflight()[CONV_A]["digest_conversation"],
                         digest.PENDING)
        with open(digest.prompt_path(), "r", encoding="utf-8") as fh:
            self.assertIn(CONV_A, fh.read())
        self.assertIn(CONV_A, out)

    def test_attach_names_the_agent(self):
        self.queue_conversation(CONV_A)
        self.prepare()

        self.assertEqual(self.attach(SPAWNED), 0)

        self.assertEqual(self.inflight()[CONV_A]["digest_conversation"],
                         SPAWNED)

    def test_attach_excludes_the_agent_from_later_digests(self):
        # Without this the digest summarizes its own agent and requeues it.
        self.queue_conversation(CONV_A)
        self.prepare()
        self.attach(SPAWNED)

        self.assertIn(SPAWNED, digest.read_exclusions())

    def test_attach_without_a_pending_batch_fails(self):
        self.assertEqual(self.attach(SPAWNED), 1)

    def test_attach_without_an_id_fails(self):
        self.queue_conversation(CONV_A)
        self.prepare()

        self.assertEqual(self.attach(""), 2)
        self.assertEqual(self.inflight()[CONV_A]["digest_conversation"],
                         digest.PENDING)

    def test_unattached_batch_is_recovered_not_lost(self):
        """An agent that was prepared for but never started must come back."""
        self.queue_conversation(CONV_A)
        self.prepare()
        self.assertEqual(self.dirty_ids(), [])

        # A placeholder names no transcript, so past the grace period there is
        # no evidence of an agent working and the batch is reclaimed.
        self.age_inflight(GRACE + AGENT_IDLE + 60)
        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.tracked_ids(), [CONV_A])
        self.assertEqual(self.inflight()[CONV_A]["attempts"], 2)


class RecoveryTest(QueueTestCase):

    def test_unconfirmed_work_is_requeued_after_grace(self):
        """An agent that never upserts must not take the transcript with it."""
        self.queue_conversation(CONV_A)
        self.run_digest()
        self.age_inflight(GRACE + 60)

        self.assertEqual(self.run_digest(), 0)

        # Handed out a second time rather than forgotten.
        self.assertEqual(self.tracked_ids(), [CONV_A])
        self.assertEqual(self.inflight()[CONV_A]["attempts"], 2)

    def test_inflight_is_left_alone_during_grace(self):
        """A working agent must not have its batch handed to someone else."""
        self.queue_conversation(CONV_A)
        self.run_digest()

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.dirty_ids(), [])
        self.assertEqual(self.inflight()[CONV_A]["attempts"], 1)

    def test_slow_agent_keeps_its_batch(self):
        """A big batch takes a while. Slow is not the same as dead."""
        self.queue_conversation(CONV_A)
        self.run_digest()
        self.age_inflight(GRACE + 60)
        # The agent is past the grace period but still writing its transcript.
        self.write_agent_transcript(idle=0)

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.inflight()[CONV_A]["attempts"], 1)
        self.assertEqual(self.dirty_ids(), [])

    def test_silent_agent_loses_its_batch(self):
        """An agent that has written nothing for a long time is gone."""
        self.queue_conversation(CONV_A)
        self.run_digest()
        self.age_inflight(GRACE + 60)
        self.write_agent_transcript(idle=AGENT_IDLE + 60)

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.inflight()[CONV_A]["attempts"], 2)

    def test_wedged_agent_does_not_hold_a_batch_forever(self):
        """Looking busy indefinitely must not pin the records."""
        self.queue_conversation(CONV_A)
        self.run_digest()
        self.age_inflight(digest.INFLIGHT_MAX + 60)
        self.write_agent_transcript(idle=0)

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.inflight()[CONV_A]["attempts"], 2)

    def test_exhausted_records_are_parked_not_deleted(self):
        """The retry cap must not reintroduce silent loss."""
        self.queue_conversation(CONV_A)
        for _ in range(MAX_ATTEMPTS):
            self.run_digest()
            self.age_inflight(GRACE + 60)

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.dirty_ids(), [])
        self.assertEqual(self.inflight(), {})
        self.assertEqual(self.undigested_ids(), [CONV_A])
        parked = read_jsonl(self.undigested_path)[0]
        self.assertEqual(parked["attempts"], MAX_ATTEMPTS)
        self.assertIn("reason", parked)
        self.assertTrue(parked["transcript"])

    def test_indexed_conversation_is_not_requeued(self):
        """An entry naming the conversation is proof, even if --skip was lost."""
        self.queue_conversation(CONV_A)
        self.run_digest()
        self.age_inflight(GRACE + 60)
        self.write_entry([CONV_A])

        self.assertEqual(self.run_digest(), 0)

        self.assertEqual(self.tracked_ids(), [])

    def test_requeued_record_keeps_the_hook_shape(self):
        """Dispatch bookkeeping must not leak back into the dirty log."""
        self.queue_conversation(CONV_A)
        self.run_digest()
        self.age_inflight(GRACE + 60)

        queue = digest.load_queue()
        digest.recover_inflight(queue, time.time())

        self.assertNotIn("dispatched", queue[CONV_A])
        self.assertNotIn("digest_conversation", queue[CONV_A])
        self.assertEqual(queue[CONV_A]["attempts"], 1)


class LockFailureTest(QueueTestCase):
    """What happens when a queue rewrite cannot take the lock."""

    def test_handover_failure_leaves_the_records_queued(self):
        """An unrecorded handover must err towards a duplicate, not a loss."""
        self.queue_conversation(CONV_A)
        real_lock = memlib.file_lock
        acquisitions = []

        @contextlib.contextmanager
        def flaky_lock(name, timeout=None):
            acquisitions.append(name)
            # The recovery pass at the start of the run succeeds; the handover
            # after the spawn is the one that fails.
            if len(acquisitions) > 1:
                raise memlib.LockUnavailable(name)
            with real_lock(name):
                yield

        with mock.patch.object(memlib, "file_lock", flaky_lock):
            self.assertEqual(self.run_digest(), 1)

        self.assertEqual(self.dirty_ids(), [CONV_A])
        self.assertEqual(self.inflight(), {})

    def test_upsert_still_writes_the_entry(self):
        """Confirmation is best effort. Losing it costs a re-digest, not data."""
        self.queue_conversation(CONV_A)
        self.run_digest()

        def refuse(name, timeout=None):
            raise memlib.LockUnavailable(name)

        entry = {"id": "topic", "title": "t", "conversations": [CONV_A]}
        with mock.patch.object(memlib, "file_lock", refuse), \
                mock.patch.object(sys, "stdin", FakeStdin(entry)):
            self.assertEqual(upsert.main(), 0)

        self.assertEqual(len(memlib.read_index()), 1)
        # Still in flight, but the index now names it, so the next recovery
        # pass drops it instead of handing it out again.
        self.assertIn(CONV_A, self.inflight())
        self.age_inflight(GRACE + 60)
        self.run_digest()
        self.assertEqual(self.tracked_ids(), [])


class ConfirmationTest(QueueTestCase):

    def test_upsert_clears_the_conversation(self):
        self.queue_conversation(CONV_A)
        self.run_digest()

        entry = {"id": "topic", "title": "t", "conversations": [CONV_A]}
        with mock.patch.object(sys, "stdin", FakeStdin(entry)):
            self.assertEqual(upsert.main(), 0)

        self.assertEqual(self.tracked_ids(), [])

    def test_skip_clears_the_conversation(self):
        self.queue_conversation(CONV_A)
        self.run_digest()

        with mock.patch.object(sys, "argv", ["upsert.py", "--skip", CONV_A]):
            self.assertEqual(upsert.main(), 0)

        self.assertEqual(self.tracked_ids(), [])
        # A skip records nothing, it only acknowledges.
        self.assertEqual(memlib.read_index(), [])

    def test_skip_without_an_id_is_rejected(self):
        with mock.patch.object(sys, "argv", ["upsert.py", "--skip"]):
            self.assertEqual(upsert.main(), 1)

    def test_unrelated_upsert_leaves_the_queue_alone(self):
        self.queue_conversation(CONV_A)
        self.run_digest()

        entry = {"id": "topic", "title": "t", "conversations": [CONV_B]}
        with mock.patch.object(sys, "stdin", FakeStdin(entry)):
            self.assertEqual(upsert.main(), 0)

        self.assertEqual(self.tracked_ids(), [CONV_A])


class LockTest(QueueTestCase):

    def test_lock_is_exclusive_within_the_process(self):
        with memlib.file_lock("queues"):
            with self.assertRaises(memlib.LockUnavailable):
                with memlib.file_lock("queues", timeout=0.1):
                    pass

    def test_lock_is_released_after_an_error(self):
        with self.assertRaises(ValueError):
            with memlib.file_lock("queues"):
                raise ValueError("boom")
        with memlib.file_lock("queues", timeout=0.1):
            pass

    def test_hook_append_survives_a_held_lock(self):
        """A wedged lock must not cost the Stop hook its record."""
        import mark_dirty

        payload = {
            "conversationId": CONV_A,
            "workspacePaths": ["/tmp/workspace"],
            "transcriptPath": "/tmp/t.jsonl",
        }
        # flock is per open file description, so a second acquire in this
        # process blocks exactly as another process would.
        with memlib.file_lock("queues"):
            with mock.patch.object(sys, "stdin", FakeStdin(payload)):
                mark_dirty.main()

        self.assertEqual(self.dirty_ids(), [CONV_A])

    def test_second_digest_run_dispatches_nothing(self):
        """Two schedulers must not hand the same batch to two agents."""
        self.queue_conversation(CONV_A)
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="started " + SPAWNED, stderr="")

        with memlib.file_lock("digest"):
            with mock.patch.object(digest.subprocess, "run", fake_run):
                code = digest.main()

        # Exit 0: the other run is doing the work, nothing went wrong here.
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(self.dirty_ids(), [CONV_A])
        self.assertEqual(self.inflight(), {})

    def test_digest_lock_does_not_block_the_stop_hook(self):
        # The run lock is held for minutes. If the hook shared it, every turn
        # would stall behind a digest.
        import mark_dirty

        payload = {"conversationId": CONV_B, "workspacePaths": ["/tmp/w"]}
        with memlib.file_lock("digest"):
            with mock.patch.object(sys, "stdin", FakeStdin(payload)):
                mark_dirty.main()
        self.assertEqual(self.dirty_ids(), [CONV_B])

    def test_upsert_writes_even_when_the_index_lock_is_held(self):
        # Losing the entry is worse than the lost update the lock prevents.
        entry = {"id": "topic", "title": "t", "summary": "s",
                 "conversations": [CONV_A]}
        with memlib.file_lock("index"):
            with mock.patch.object(sys, "stdin", FakeStdin(entry)):
                self.assertEqual(upsert.main(), 0)
        self.assertEqual([e["id"] for e in memlib.read_index()], ["topic"])


class FakeStdin:
    """Minimal stand-in for sys.stdin holding one JSON object."""

    def __init__(self, obj):
        self._text = json.dumps(obj)

    def read(self):
        return self._text


if __name__ == "__main__":
    unittest.main()
