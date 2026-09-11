"""Shared helpers for the conversation memory system.

Nothing here may raise into a hook. Callers in hook context must wrap use of
this module in try/except and always emit valid JSON on stdout.
"""

import json
import math
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".gemini", "config")

# scripts/ -> memory/ -> repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# All accumulated state lives under the repo's gitignored tmp/ directory. The
# repo itself stays a clean skeleton: nothing the agent writes at runtime can
# be committed, which matters because the remote is public.
TMP_DIR = os.path.join(REPO_ROOT, "tmp")
MEMORY_DIR = os.path.join(TMP_DIR, "memory")
GENERATED_SKILLS_DIR = os.path.join(TMP_DIR, "skills")
INDEX_PATH = os.path.join(MEMORY_DIR, "index.jsonl")
DIRTY_PATH = os.path.join(MEMORY_DIR, "dirty.jsonl")
ACCESS_LOG = os.path.join(MEMORY_DIR, "access.log")
# What was injected into which conversation, so the digest can judge
# afterwards whether any of it actually helped.
INJECTIONS_PATH = os.path.join(MEMORY_DIR, "injections.jsonl")
PROPOSALS_DIR = os.path.join(MEMORY_DIR, "proposals")


def _find_app_data_dir():
    """Locate the editor's data directory without hardcoding its name.

    The directory sits under ~/.gemini and is named after the build, which
    varies between installs and channels. Rather than pin one name, look for
    the layout: a directory holding both conversations/ and brain/. Several
    can exist side by side when more than one build has been run, so prefer
    the most recently written one. MEMORY_APP_DATA_DIR overrides everything.
    """
    override = os.environ.get("MEMORY_APP_DATA_DIR")
    if override:
        return override

    root = os.path.join(HOME, ".gemini")
    best, best_mtime = None, -1.0
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in names:
        candidate = os.path.join(root, name)
        if not os.path.isdir(os.path.join(candidate, "conversations")):
            continue
        if not os.path.isdir(os.path.join(candidate, "brain")):
            continue
        mtime = os.path.getmtime(os.path.join(candidate, "conversations"))
        if mtime > best_mtime:
            best, best_mtime = candidate, mtime
    # Falling back to ~/.gemini keeps path joins valid; callers that need a
    # real transcript will simply find nothing rather than crash.
    return best or root


APP_DATA_DIR = _find_app_data_dir()
CONVERSATIONS_DIR = os.path.join(APP_DATA_DIR, "conversations")


def agentapi_path():
    """Resolve the agentapi binary.

    It is normally on PATH because the editor puts its bin directory there,
    but sidecars can start with a trimmed environment, so fall back to the
    bin directory beside the conversation store.
    """
    found = shutil.which("agentapi")
    if found:
        return found
    return os.path.join(APP_DATA_DIR, "bin", "agentapi")

# Conversations idle for less than this are still considered active and are
# not digested yet.
IDLE_SECONDS = 20 * 60

# Retrieval tuning.
MAX_INJECTED = 3
MIN_SCORE = 3.0
SUMMARY_CHARS = 240

_STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "was", "were", "with", "this",
    "that", "these", "those", "have", "has", "had", "not", "but", "can", "could",
    "should", "would", "will", "how", "what", "why", "when", "where", "which",
    "who", "does", "did", "doing", "done", "use", "using", "used", "need",
    "want", "please", "make", "made", "get", "got", "let", "its", "his", "her",
    "them", "they", "our", "out", "into", "from", "then", "than", "there",
    "here", "some", "any", "all", "one", "two", "new", "old", "add", "added",
    "fix", "fixed", "run", "running", "file", "files", "code", "help", "like",
    "just", "also", "more", "most", "very", "much", "such", "about", "after",
    "before", "because", "been", "being", "each", "other", "same", "only",
}


def ensure_dirs():
    os.makedirs(MEMORY_DIR, exist_ok=True)
    os.makedirs(PROPOSALS_DIR, exist_ok=True)
    os.makedirs(GENERATED_SKILLS_DIR, exist_ok=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


_WRAPPER_TAGS = re.compile(
    r"</?(USER_REQUEST|ADDITIONAL_METADATA|SYSTEM_MESSAGE)>", re.I)
_METADATA_BLOCK = re.compile(
    r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", re.I | re.S)
_ACTION_PREAMBLE = re.compile(
    r"^The USER performed the following action:.*?(?=<USER_REQUEST>|$)",
    re.I | re.S)


def clean_query(text):
    """Strip the scaffolding the harness wraps around a user message.

    A raw user turn arrives carrying <USER_REQUEST> tags, an
    <ADDITIONAL_METADATA> block listing open documents, and sometimes a file
    listing from a viewer action. Those contribute dozens of tokens that have
    nothing to do with the question, which both dilutes scoring and produces
    spurious matches on incidental file names.
    """
    if not text:
        return ""
    text = _METADATA_BLOCK.sub(" ", text)
    text = _ACTION_PREAMBLE.sub(" ", text)
    text = _WRAPPER_TAGS.sub(" ", text)
    return text


# Suffixes stripped longest-first so "traversals" reduces before "traversal".
_SUFFIXES = ("ization", "isation", "ingly", "edly", "ments", "ment", "ions",
             "ing", "ers", "er", "ies", "ied", "ed", "es", "s", "ly")


def stem(token):
    """Crude suffix stripping.

    Not linguistically correct, and not meant to be. The goal is only that
    "traversal" and "traversals", or "fetching" and "fetch", collapse to the
    same key so a paraphrased question still lands on the right entry.
    """
    if any(c.isdigit() for c in token):
        return token
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def tokenize(text, with_stems=True):
    """Lowercase alphanumeric tokens, stopwords dropped.

    Identifier-like tokens containing digits (utf8, v3.2, cwe79) are kept even
    when short, because they carry most of the retrieval signal.
    """
    if not text:
        return set()
    raw = re.split(r"[^A-Za-z0-9_]+", text.lower())
    out = set()
    for tok in raw:
        if not tok:
            continue
        has_digit = any(c.isdigit() for c in tok)
        if has_digit and len(tok) >= 2:
            out.add(tok)
            continue
        if len(tok) < 3 or tok in _STOPWORDS:
            continue
        out.add(tok)
        if with_stems:
            out.add(stem(tok))
        # Split snake_case parts so render_flex_overflow also matches overflow.
        if "_" in tok:
            for part in tok.split("_"):
                if len(part) >= 2 and part not in _STOPWORDS:
                    out.add(part)
                    if with_stems:
                        out.add(stem(part))
    return out


def read_index():
    entries = []
    if not os.path.exists(INDEX_PATH):
        return entries
    with open(INDEX_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def write_index(entries):
    """Atomic replace so a crash mid-write cannot truncate the index."""
    ensure_dirs()
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.replace(tmp, INDEX_PATH)


def append_jsonl(path, obj):
    ensure_dirs()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _age_multiplier(modified):
    try:
        dt = datetime.fromisoformat(modified)
    except (TypeError, ValueError):
        return 0.7
    days = (datetime.now(timezone.utc) - dt).days
    if days < 30:
        return 1.0
    if days < 90:
        return 0.85
    if days < 365:
        return 0.7
    return 0.55


def entry_tokens(entry):
    """Every searchable token of an entry, grouped by field weight."""
    kw = set()
    for k in entry.get("keywords", []) + entry.get("aliases", []):
        kw |= tokenize(k)
    title = tokenize(entry.get("title", ""))
    summary = tokenize(entry.get("summary", ""))
    for point in entry.get("points", []):
        summary |= tokenize(point)
    basenames = set()
    for path in entry.get("files", []):
        basenames |= tokenize(os.path.basename(path))
    return kw, title, summary, basenames


def build_idf(entries):
    """Inverse document frequency over the index.

    Without this every matched token counts the same, so an entry sharing the
    word "memory" with the query scores as highly as one sharing "m229". Rare
    tokens are the ones that actually identify a topic.
    """
    total = max(len(entries), 1)
    counts = {}
    for entry in entries:
        seen = set()
        for group in entry_tokens(entry):
            seen |= group
        for token in seen:
            counts[token] = counts.get(token, 0) + 1
    idf = {}
    for token, count in counts.items():
        # Ranges from ~1.0 for a token in every entry to ~log(N)+1 for a
        # token in exactly one.
        idf[token] = 1.0 + math.log(total / float(count))
    return idf


def _weighted(overlap, idf):
    return sum(idf.get(token, 1.0) for token in overlap)


def feedback_multiplier(entry):
    """Scale by how often this entry proved useful when it was injected.

    Laplace smoothed so a brand new entry sits at 1.0 and needs evidence in
    either direction to move. Range is roughly 0.5 to 1.5.
    """
    useful = int(entry.get("useful", 0) or 0)
    useless = int(entry.get("useless", 0) or 0)
    if useful == 0 and useless == 0:
        return 1.0
    ratio = (useful + 1.0) / (useful + useless + 2.0)
    return 0.5 + ratio


def is_suppressed(entry):
    """Retire an entry that keeps being injected and never helps."""
    useful = int(entry.get("useful", 0) or 0)
    useless = int(entry.get("useless", 0) or 0)
    return useful == 0 and useless >= 5


def score_entry(entry, query_tokens, workspace, idf=None):
    """IDF weighted token overlap. Keywords dominate, prose is a weak signal."""
    if idf is None:
        idf = {}
    kw, title, summary, basenames = entry_tokens(entry)

    score = (
        3.0 * _weighted(query_tokens & kw, idf)
        + 2.0 * _weighted(query_tokens & title, idf)
        + 2.0 * _weighted(query_tokens & basenames, idf)
        + 1.0 * _weighted(query_tokens & summary, idf)
    )
    if score == 0:
        return 0.0

    workspaces = entry.get("workspaces", [])
    if workspace and workspaces:
        if any(workspace.startswith(w) or w.startswith(workspace) for w in workspaces):
            score += 2.0
        else:
            score *= 0.5

    return (score
            * _age_multiplier(entry.get("modified", ""))
            * feedback_multiplier(entry))


def search(query, workspace=None, limit=MAX_INJECTED, min_score=MIN_SCORE,
           exclude_conversation=None):
    tokens = tokenize(clean_query(query))
    if not tokens:
        return []
    entries = read_index()
    idf = build_idf(entries)
    results = []
    for entry in entries:
        if exclude_conversation and exclude_conversation in entry.get("conversations", []):
            continue
        if is_suppressed(entry):
            continue
        s = score_entry(entry, tokens, workspace, idf)
        if s >= min_score:
            results.append((s, entry))
    results.sort(key=lambda pair: pair[0], reverse=True)
    return results[:limit]


def last_user_input(transcript_path):
    """Return the text of the most recent USER_INPUT step in a transcript."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    latest = ""
    with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or "USER_INPUT" not in line:
                continue
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue
            if step.get("type") == "USER_INPUT":
                latest = step.get("content", "") or latest
    return latest


def count_user_inputs(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return 0
    count = 0
    with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"type":"USER_INPUT"' in line or '"type": "USER_INPUT"' in line:
                count += 1
    return count


def is_subagent(conversation_id):
    """A conversation with parent references is a subagent trajectory.

    Returns False when the answer cannot be determined, so that an unknown
    conversation is digested rather than silently dropped.
    """
    db = os.path.join(CONVERSATIONS_DIR, conversation_id + ".db")
    if not os.path.exists(db):
        return False
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2.0)
        try:
            row = con.execute("SELECT count(*) FROM parent_references").fetchone()
            return bool(row and row[0] > 0)
        finally:
            con.close()
    except sqlite3.Error:
        return False


def transcript_path_for(conversation_id):
    return os.path.join(
        APP_DATA_DIR, "brain", conversation_id,
        ".system_generated", "logs", "transcript.jsonl")


def log_access(entry_ids):
    """Record retrieval hits out of band.

    The retrieval hook must not write to index.jsonl, because the digest
    rewrites that file and the two would race. The digest folds this log into
    the accessed timestamps on its next run.
    """
    if not entry_ids:
        return
    ensure_dirs()
    with open(ACCESS_LOG, "a", encoding="utf-8") as fh:
        for eid in entry_ids:
            fh.write("%f\t%s\n" % (time.time(), eid))
