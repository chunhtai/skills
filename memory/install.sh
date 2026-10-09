#!/usr/bin/env bash
# Wire the conversation memory system into the local agent config.
#
# Everything executable lives in this repo and is committed. Everything the
# agent accumulates at runtime lives in the repo's gitignored tmp/ directory:
# the index, the digest queue, and any machine-generated skills. The committed
# tree stays a clean skeleton.
#
# The config directory only gets what the runtime insists on finding there:
# hooks.json, and a symlink per sidecar pointing at the definition in this
# repo. hooks.json names the scripts by absolute path. Either way, editing a
# script or a sidecar definition here takes effect without reinstalling.
#
# Safe to run repeatedly.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${REPO_DIR}/.." && pwd)"
CONFIG_DIR="${HOME}/.gemini/config"
TMP_DIR="${REPO_ROOT}/tmp"

echo "repo:   ${REPO_ROOT}"
echo "state:  ${TMP_DIR}  (gitignored)"
echo "config: ${CONFIG_DIR}"

mkdir -p "${TMP_DIR}/memory/proposals" "${TMP_DIR}/skills"
mkdir -p "${CONFIG_DIR}/sidecars"

# Refuse to run if tmp/ is not actually ignored. Without that guarantee the
# promotion sidecar would be writing committable files into a public repo.
if ! git -C "${REPO_ROOT}" check-ignore -q tmp; then
  echo "ERROR: ${TMP_DIR} is not gitignored. Add 'tmp/' to ${REPO_ROOT}/.gitignore" >&2
  exit 1
fi
echo "  ok: tmp/ is gitignored"

# hooks.json is shared with any other hook you may add, so merge rather than
# overwrite. Only the conversation-memory key is touched.
python3 - "$REPO_DIR" "$CONFIG_DIR" <<'PY'
import json
import os
import sys

repo_dir, config_dir = sys.argv[1], sys.argv[2]
source = os.path.join(repo_dir, "config", "hooks.json")
target = os.path.join(config_dir, "hooks.json")

with open(source, "r", encoding="utf-8") as fh:
    incoming = json.load(fh)

existing = {}
if os.path.exists(target):
    try:
        with open(target, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
    except json.JSONDecodeError:
        backup = target + ".broken"
        os.replace(target, backup)
        print("existing hooks.json was not valid JSON, moved to %s" % backup)

for key, value in incoming.items():
    if key in existing and existing[key] != value:
        print("replacing existing hook: %s" % key)
    existing[key] = value

tmp = target + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(existing, fh, indent=2)
    fh.write("\n")
os.replace(tmp, target)
print("wrote %s" % target)
PY

# Each sidecar directory is symlinked, not copied, so editing sidecar.json in
# this repo takes effect without reinstalling. That is also why the script
# paths inside sidecar.json are relative: the runtime sets the working
# directory to the sidecar folder, which resolves through the link back into
# the repo.
for name in memory-digest memory-promote; do
  link="${CONFIG_DIR}/sidecars/${name}"
  target="${REPO_DIR}/config/sidecars/${name}"

  if [ -L "$link" ]; then
    rm "$link"
  elif [ -e "$link" ]; then
    # An older install wrote a real directory here. Park it rather than
    # delete it, but park it outside sidecars/ so the runtime does not load
    # it as a second copy of the same schedule.
    mv "$link" "${CONFIG_DIR}/${name}.replaced.$(date +%s)"
    echo "  moved the previous ${name} directory out of sidecars/"
  fi

  ln -s "$target" "$link"
  echo "linked ${link} -> ${target}"
done


# Machine-generated skills live in tmp/skills. They still have to be
# discoverable or promotion is pointless, so register that path too.
python3 - "$REPO_ROOT" "$CONFIG_DIR" <<'PY'
import json
import os
import sys

repo_root, config_dir = sys.argv[1], sys.argv[2]
target = os.path.join(config_dir, "skills.json")
generated = os.path.join(repo_root, "tmp", "skills")
skeleton = os.path.join(repo_root, "skills")

config = {"entries": []}
if os.path.exists(target):
    try:
        with open(target, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except json.JSONDecodeError:
        print("skills.json is not valid JSON, leaving it alone")
        raise SystemExit(0)

entries = config.setdefault("entries", [])
paths = {entry.get("path") for entry in entries}

for path in (skeleton, generated):
    if path not in paths:
        entries.append({"path": path})
        print("registered %s" % path)

tmp = target + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2)
    fh.write("\n")
os.replace(tmp, target)
PY

echo
echo "Self test."
python3 "${REPO_DIR}/scripts/search.py" --list >/dev/null && echo "  ok: search runs"
echo '{"invocationNum":2}' | python3 "${REPO_DIR}/scripts/retrieve.py" >/dev/null \
  && echo "  ok: retrieve hook runs"
echo 'garbage' | python3 "${REPO_DIR}/scripts/mark_dirty.py" >/dev/null \
  && echo "  ok: stop hook survives bad input"
python3 "${REPO_DIR}/scripts/digest.py" --dry-run >/dev/null && echo "  ok: digest runs"

untracked="$(git -C "${REPO_ROOT}" status --porcelain --ignored=no -- tmp | wc -l | tr -d ' ')"
if [ "${untracked}" = "0" ]; then
  echo "  ok: nothing under tmp/ is visible to git"
else
  echo "  WARNING: git can see files under tmp/" >&2
fi

echo
echo "Done. Reload the editor window so the watcher picks up the hooks and sidecars."
