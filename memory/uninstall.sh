#!/usr/bin/env bash
# Remove the conversation memory system from the local agent config.
#
# By default this only unwires it: the hooks, the sidecars, and the skills.json
# registration go away, while the accumulated index under tmp/ is left alone so
# a later reinstall picks up where it left off.
#
# Pass --purge to also delete tmp/memory and tmp/skills.
#
# Nothing in the committed tree is touched. Safe to run repeatedly, and safe to
# run when the system was never installed.

set -euo pipefail

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      echo "usage: $0 [--purge]"
      echo "  --purge  also delete the memory index and generated skills"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${REPO_DIR}/.." && pwd)"
CONFIG_DIR="${HOME}/.gemini/config"
TMP_DIR="${REPO_ROOT}/tmp"

echo "repo:   ${REPO_ROOT}"
echo "config: ${CONFIG_DIR}"
echo

# Drop only the conversation-memory key. Any other hook you added stays.
python3 - "$CONFIG_DIR" <<'PY'
import json
import os
import sys

config_dir = sys.argv[1]
target = os.path.join(config_dir, "hooks.json")

if not os.path.exists(target):
    print("no hooks.json, nothing to unhook")
    raise SystemExit(0)

try:
    with open(target, "r", encoding="utf-8") as fh:
        hooks = json.load(fh)
except json.JSONDecodeError:
    print("hooks.json is not valid JSON, leaving it alone")
    raise SystemExit(0)

if hooks.pop("conversation-memory", None) is None:
    print("hooks.json has no conversation-memory entry")
    raise SystemExit(0)

if hooks:
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(hooks, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, target)
    print("removed the conversation-memory hook, kept %d other hook(s)" % len(hooks))
else:
    os.remove(target)
    print("removed the conversation-memory hook and deleted the now-empty hooks.json")
PY

for name in memory-digest memory-promote; do
  dir="${CONFIG_DIR}/sidecars/${name}"
  # Test -L first. install.sh links this at the repo, and rm -rf on a symlink
  # would still only drop the link, but being explicit keeps it obvious that
  # nothing in the repo is at risk here.
  if [ -L "$dir" ]; then
    rm "$dir"
    echo "unlinked ${dir}"
  elif [ -d "$dir" ]; then
    rm -rf "$dir"
    echo "removed ${dir}"
  else
    echo "no ${dir}"
  fi
done

# Unregister the generated skills path. The hand-maintained skills/ entry is
# left in place; it may well predate this system.
python3 - "$REPO_ROOT" "$CONFIG_DIR" <<'PY'
import json
import os
import sys

repo_root, config_dir = sys.argv[1], sys.argv[2]
target = os.path.join(config_dir, "skills.json")
generated = os.path.join(repo_root, "tmp", "skills")

if not os.path.exists(target):
    print("no skills.json")
    raise SystemExit(0)

try:
    with open(target, "r", encoding="utf-8") as fh:
        config = json.load(fh)
except json.JSONDecodeError:
    print("skills.json is not valid JSON, leaving it alone")
    raise SystemExit(0)

entries = config.get("entries", [])
kept = [entry for entry in entries if entry.get("path") != generated]
if len(kept) == len(entries):
    print("skills.json does not register %s" % generated)
    raise SystemExit(0)

config["entries"] = kept
tmp = target + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2)
    fh.write("\n")
os.replace(tmp, target)
print("unregistered %s" % generated)
PY

echo
if [ "$PURGE" = "1" ]; then
  for dir in "${TMP_DIR}/memory" "${TMP_DIR}/skills"; do
    if [ -d "$dir" ]; then
      rm -rf "$dir"
      echo "purged ${dir}"
    fi
  done
  rmdir "${TMP_DIR}" 2>/dev/null && echo "removed empty ${TMP_DIR}" || true
else
  if [ -d "${TMP_DIR}/memory" ]; then
    entries="$(wc -l < "${TMP_DIR}/memory/index.jsonl" 2>/dev/null | tr -d ' ' || echo 0)"
    echo "kept ${TMP_DIR} (${entries} index entries). Re-run with --purge to delete it."
  fi
fi

echo
echo "Verifying."
if grep -q "conversation-memory" "${CONFIG_DIR}/hooks.json" 2>/dev/null; then
  echo "  WARNING: hooks.json still mentions conversation-memory" >&2
else
  echo "  ok: no memory hooks registered"
fi
if ls -d "${CONFIG_DIR}"/sidecars/memory-* >/dev/null 2>&1; then
  echo "  WARNING: a memory sidecar directory remains" >&2
else
  echo "  ok: no memory sidecars"
fi

echo
echo "Done. Reload the editor window so the watcher drops the hooks and sidecars."
echo "The code under ${REPO_DIR} is untouched; run install.sh to wire it back up."
