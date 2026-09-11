# Conversation memory

Captures finished conversations into a keyword-searchable index, injects
matching entries into new conversations, and promotes recurring corrections
into skills.

## Install

```bash
./install.sh
```

Then reload the editor window. The installer is idempotent and merges into an
existing `hooks.json` rather than replacing it.

## Layout

The committed tree is a skeleton of agent configuration. Everything the agent
accumulates goes to `tmp/`, which is gitignored. This repo has a public
remote, and the promotion sidecar writes files autonomously, so nothing it
produces may be committable.

    memory/
      scripts/          all executable code
      config/           templates copied into ~/.gemini/config by install.sh
      install.sh
    skills/             hand-maintained skills, committed
    rules/              hand-maintained rules, committed

    tmp/                gitignored, never pushed
      memory/index.jsonl        the index
      memory/dirty.jsonl        conversations waiting to be digested
      memory/access.log         retrieval hits, folded in by the digest
      memory/injections.jsonl   what was injected where, graded by the digest
      memory/retrieve.log       one line per hook call, for tuning
      memory/seen/              hash of the last user message per conversation
      memory/exclude.txt        conversations this system created
      memory/promoted.json      patterns already turned into skills
      memory/proposals/         rule suggestions awaiting review
      memory/proposals/quarantine/  generated skills that tripped the secret scan
      skills/                   machine-generated skills

    ~/.gemini/config/
      hooks.json                merged, points back at memory/scripts/
      sidecars/memory-digest/   points back at memory/scripts/
      sidecars/memory-promote/
      skills.json               registers both skills/ and tmp/skills

`tmp/skills` is registered in `skills.json` so generated skills still load.
They are active but never committed. Move one into `skills/` by hand if you
decide to keep it.

## Pieces

| Piece | Trigger | File |
| :--- | :--- | :--- |
| Queue a conversation | `Stop` hook, every turn | `scripts/mark_dirty.py` |
| Inject matching memories | `PreInvocation` hook, once per new user message | `scripts/retrieve.py` |
| Summarize into the index | `memory-digest` sidecar, every 4h | `scripts/digest.py` |
| Promote corrections | `memory-promote` sidecar, Mondays 09:00 | `scripts/promote.py` |
| Manual search | `search-conversation-memory` skill | `scripts/search.py` |
| Merge an entry | called by the digest agent | `scripts/upsert.py` |
| Grade an injection | called by the digest agent | `scripts/feedback.py` |

## Why it is shaped this way

Hooks block the agent loop, so neither hook does real work. `mark_dirty.py`
appends one line. `retrieve.py` greps a local file. Summarization happens in a
sidecar, out of band.

There is no conversation-end event. `Stop` fires once per turn, so the queue
holds every conversation you have touched and the digest waits for 20 minutes
of silence before deciding a conversation is finished.

The digest spawns one conversation per run, not one per transcript, so a busy
day adds about six background conversations rather than fifty.

Retrieval scores an entry by the overlap between the query and the entry's
title, keywords, and aliases. Overlapping words are weighted by inverse
document frequency, so a rare identifier counts for far more than a word that
appears in half the index, and both sides are crudely stemmed so "overflowing"
reaches "overflow". The digest prompt still insists on literal identifiers in
`keywords`, because IDF can only rank words that are present: a summary that
says "a layout sizing error" will never match "RenderFlex overflowed". The
`aliases` field exists for the reverse case, capturing how you would describe
the problem before you know its name.

The query arrives wrapped in `<USER_REQUEST>` tags and an editor-state
metadata block, all of which is stripped before tokenizing. Left in, the
boilerplate outnumbered the real words by roughly ten to one and every entry
scored about the same.

Injected memories carry a date and a warning to verify against current code.
A stale memory that is trusted is worse than no memory.

Every injection is recorded to `injections.jsonl`. The next digest reads the
transcript, decides whether each injected entry was actually used, and calls
`feedback.py --used` or `--noise`. Those counts become a multiplier of roughly
0.5 to 1.5 on future scores, and an entry with five noise marks and no useful
ones stops being injected at all. This rests on an unverified assumption: that
the ephemeral injected message is visible in the transcript the digest reads.
If it is not, everything grades as noise and entries will suppress themselves.
Check `feedback.py --report` after the first few digests before trusting it.

The digest excludes conversations it spawned itself. Without `exclude.txt` it
would summarize its own output and requeue forever.

`promote.py` writes generated skills into `tmp/skills`, which git ignores, and
never runs `git add` or `git commit`. The promotion agent reads raw
transcripts, which routinely hold credentials, internal host names, and paths
from unrelated projects, so its prompt forbids verbatim copying and every file
it writes is scanned for key-shaped strings afterwards. Anything that trips
the scan is moved to `tmp/memory/proposals/quarantine/`, because a skill file
is loaded into future prompts and a credential in one would keep resurfacing.

Preference corrections never touch `rules/general.md` automatically, because
that file is loaded into every conversation.

## Operating it

Check what is queued without spawning anything:

    python3 scripts/digest.py --dry-run

Force a digest now:

    python3 scripts/digest.py

Inspect the index:

    python3 scripts/search.py --list
    python3 scripts/search.py "some query"

See which entries are earning their place:

    python3 scripts/feedback.py --report

An entry with a high useless count is either badly keyworded or too general.
Fix its `keywords` and `aliases` with `upsert.py`, or delete the line from
`index.jsonl`. `retrieve.log` shows what each hook call matched, which is the
faster way to tell a scoring problem from a summarization problem.

## Pausing it

To stop it temporarily without unwiring anything, set `"enabled": false` on
the `conversation-memory` object in `~/.gemini/config/hooks.json`, and disable
the two sidecars from the sidecar UI.

## Uninstalling

```bash
./uninstall.sh           # unwire, keep the accumulated index
./uninstall.sh --purge   # also delete tmp/memory and tmp/skills
```

It removes the `conversation-memory` hook, the two sidecar directories, and
the `tmp/skills` entry in `skills.json`. Other hooks in `hooks.json` are kept,
and `hooks.json` is deleted only if nothing else is left in it. The
hand-maintained `skills/` registration is left alone, since it likely predates
this system.

Without `--purge` the index survives, so reinstalling later resumes with the
memory intact. Nothing in the committed tree is touched either way, so
`install.sh` puts it all back.

Both scripts are idempotent and safe to run when the system is not installed.

Reload the editor window afterwards so the watcher drops the hooks and
sidecars.

