#!/usr/bin/env python3
"""Promote recurring corrections into skills or rule suggestions.

Run weekly by the memory-promote sidecar. Groups the corrections recorded in
the memory index by similarity. A pattern that recurs across enough distinct
conversations is worth encoding; a one-off is not.

Routing:
  kind == "procedure"   -> a skill, written into the skills repo
  kind == "preference"  -> a suggested edit to the rules file, never applied

Skills are written directly but never staged or committed, so `git status`
shows exactly what was machine-generated and `git clean` reverts it.
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
# Machine-generated skills go to the gitignored tmp/ tree, never to the
# committed skills/ skeleton. The remote is public and this writer is
# autonomous, so nothing it produces may be committable.
SKILLS_TARGET = memlib.GENERATED_SKILLS_DIR
RULES_FILE = os.path.join(memlib.REPO_ROOT, "rules", "general.md")
PROMOTED_PATH = os.path.join(memlib.MEMORY_DIR, "promoted.json")
EXCLUDE_PATH = os.path.join(memlib.MEMORY_DIR, "exclude.txt")
AGENTAPI = memlib.agentapi_path()

# How many distinct conversations must show the same correction before it is
# worth encoding.
RECURRENCE_THRESHOLD = 3
# Token overlap ratio above which two corrections are the same pattern.
SIMILARITY = 0.5

_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def load_promoted():
    if not os.path.exists(PROMOTED_PATH):
        return []
    try:
        with open(PROMOTED_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []


def save_promoted(promoted):
    memlib.ensure_dirs()
    tmp = PROMOTED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(promoted, fh, indent=2)
    os.replace(tmp, PROMOTED_PATH)


def collect():
    """Flatten every correction in the index, tagged with its conversation."""
    items = []
    for entry in memlib.read_index():
        convs = entry.get("conversations", []) or [entry.get("id", "")]
        for correction in entry.get("corrections", []) or []:
            should = (correction.get("should") or "").strip()
            if not should:
                continue
            items.append({
                "did": (correction.get("did") or "").strip(),
                "should": should,
                "kind": (correction.get("kind") or "procedure").strip().lower(),
                "topic": entry.get("id", ""),
                "conversations": convs,
                "tokens": memlib.tokenize(should + " " + (correction.get("did") or "")),
            })
    return items


def similar(a, b):
    if not a or not b:
        return False
    overlap = len(a & b)
    return overlap / float(min(len(a), len(b))) >= SIMILARITY


def cluster(items):
    """Greedy single-pass grouping. Good enough for tens of corrections."""
    groups = []
    for item in items:
        for group in groups:
            if group["kind"] == item["kind"] and similar(group["tokens"], item["tokens"]):
                group["items"].append(item)
                group["tokens"] = group["tokens"] | item["tokens"]
                break
        else:
            groups.append({
                "kind": item["kind"],
                "tokens": set(item["tokens"]),
                "items": [item],
            })
    return groups


def distinct_conversations(group):
    convs = set()
    for item in group["items"]:
        convs.update(item["conversations"])
    return convs


def signature(group):
    """Stable key so a pattern is not promoted twice."""
    return "|".join(sorted(list(group["tokens"]))[:12])


def describe(group):
    lines = []
    for item in group["items"]:
        lines.append("- did: %s\n  should: %s\n  topic: %s"
                     % (item["did"], item["should"], item["topic"]))
    return "\n".join(lines)


SKILL_PROMPT = """A correction has recurred across {count} separate conversations. Turn it into a skill. Work autonomously and do not ask questions.

The recurring correction:

{description}

Steps:

1. Read the transcripts if you need detail: {conversations}
2. Check {target} and {skeleton} for a skill that already covers this. If one
   exists in {target}, improve it in place. If the matching skill is in
   {skeleton}, do not edit it; that tree is hand-maintained. Write your version
   to {target} instead and say so in your reply.
3. Otherwise create {target}/<kebab-name>/SKILL.md with YAML frontmatter
   holding `name` and `description`, then the procedure in markdown.

The description field is what decides whether the skill ever gets used, so it
must state the triggering situation in the words a future task would use, not
a summary of the contents.

Keep the skill narrow. It should encode the specific procedure that was
corrected, not a general essay on the topic.

Write the procedure in your own words. Do not paste transcript content
verbatim. Transcripts contain credentials, tokens, absolute paths from other
projects, and internal identifiers, none of which belong in a skill. If the
procedure genuinely needs a secret or a host name, write a placeholder such as
<TOKEN> and describe where the real value comes from.

Do not run `git add`, `git commit`, or any other git command that changes
state.

Reply with the path you wrote and one sentence on what it covers.
"""


SECRET_PATTERNS = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{35}"
    # The key this editor exports as GEMINI_API_KEY uses an AQ. prefix and
    # matches none of the vendor shapes above. It leaks into transcripts any
    # time something dumps the environment.
    r"|AQ\.[A-Za-z0-9_\-]{30,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\."
    # Catch-all for shapes not yet known: a credential-ish name assigned a
    # long opaque value. Placeholders are excluded so the prompt's own
    # <TOKEN> convention does not trip it.
    r"|(?i:api[_-]?key|secret|token|password|passwd)"
    r'''\s*[=:]\s*["']?'''
    r"(?!<|\$\{|xxx|your|placeholder|redacted|example|TOKEN)"
    r"[A-Za-z0-9_\-.]{24,})")


def quarantine_secrets(since):
    """Move any freshly written skill containing a key-shaped string aside.

    The gitignore already prevents these files reaching the public remote.
    This is the second line: a skill file is loaded into future prompts, so a
    credential sitting in one would keep resurfacing.
    """
    quarantine = os.path.join(memlib.PROPOSALS_DIR, "quarantine")
    flagged = []
    for root, _dirs, names in os.walk(SKILLS_TARGET):
        for name in names:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < since:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            if SECRET_PATTERNS.search(body):
                os.makedirs(quarantine, exist_ok=True)
                dest = os.path.join(quarantine, os.path.basename(root) + "-" + name)
                os.replace(path, dest)
                flagged.append(dest)
    for path in flagged:
        print("promote: quarantined a generated skill containing a "
              "credential-shaped string: %s" % path, file=sys.stderr)
    return flagged


def promote_skill(group):
    convs = sorted(distinct_conversations(group))
    prompt = SKILL_PROMPT.format(
        count=len(convs),
        description=describe(group),
        conversations=", ".join(convs[:8]),
        target=SKILLS_TARGET,
        skeleton=os.path.join(memlib.REPO_ROOT, "skills"))
    started = time.time()
    try:
        result = subprocess.run(
            [AGENTAPI, "new-conversation", "--model=pro",
             "--title=Skill promotion", prompt],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as err:
        print("promote: could not start agent: %s" % err, file=sys.stderr)
        return False
    memlib.ensure_dirs()
    with open(EXCLUDE_PATH, "a", encoding="utf-8") as fh:
        for match in _UUID.findall((result.stdout or "") + (result.stderr or "")):
            fh.write(match + "\n")
    quarantine_secrets(started)
    return True


def write_rule_suggestion(groups):
    """Preferences are never applied automatically.

    The rules file is loaded into every conversation, so an unreviewed edit
    there changes all future behaviour at once.
    """
    path = os.path.join(memlib.PROPOSALS_DIR, "rule_suggestions.md")
    memlib.ensure_dirs()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n## Suggested on %s\n\n" % memlib.now_iso()[:10])
        fh.write("Recurring preference corrections. Apply by hand to %s if you agree.\n\n"
                 % RULES_FILE)
        for group in groups:
            convs = sorted(distinct_conversations(group))
            fh.write("* Seen in %d conversations.\n" % len(convs))
            for item in group["items"]:
                fh.write("    - instead of \"%s\", %s\n" % (item["did"], item["should"]))
    return path


def main():
    memlib.ensure_dirs()
    items = collect()
    if not items:
        return 0

    promoted = load_promoted()
    known = {record["signature"] for record in promoted}

    ready = []
    for group in cluster(items):
        if len(distinct_conversations(group)) < RECURRENCE_THRESHOLD:
            continue
        if signature(group) in known:
            continue
        ready.append(group)

    if not ready:
        return 0

    procedures = [g for g in ready if g["kind"] == "procedure"]
    preferences = [g for g in ready if g["kind"] != "procedure"]

    for group in procedures:
        if promote_skill(group):
            promoted.append({
                "signature": signature(group),
                "kind": "procedure",
                "at": memlib.now_iso(),
            })

    if preferences:
        path = write_rule_suggestion(preferences)
        print("promote: wrote rule suggestions to %s" % path)
        for group in preferences:
            promoted.append({
                "signature": signature(group),
                "kind": "preference",
                "at": memlib.now_iso(),
            })

    save_promoted(promoted)
    print("promote: handled %d pattern(s)" % len(ready))
    return 0


if __name__ == "__main__":
    sys.exit(main())
