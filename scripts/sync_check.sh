#!/usr/bin/env bash
# Checks whether ~/Documents/llm_brain is in sync with upstream (NAS).
# Exits 0 if in sync, 1 if behind or diverged.

DATA_DIR="${HOME}/Documents/llm_brain"

if [ ! -d "${DATA_DIR}/.git" ]; then
  echo "SYNC WARNING: ${DATA_DIR} is not a git repository."
  exit 1
fi

cd "${DATA_DIR}" || exit 1

# Fetch quietly; fail fast if the remote is unreachable.
if ! git fetch nas --quiet 2>/dev/null; then
  echo "SYNC WARNING: Could not reach NAS remote. Working offline — data may be stale."
  exit 0
fi

LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse "@{u}" 2>/dev/null)
BASE=$(git merge-base @ "@{u}" 2>/dev/null)

if [ -z "${REMOTE}" ]; then
  echo "SYNC INFO: No upstream tracking branch set. Skipping sync check."
  exit 0
fi

if [ "${LOCAL}" = "${REMOTE}" ]; then
  echo "SYNC OK: Data is up to date with NAS."
  exit 0
elif [ "${LOCAL}" = "${BASE}" ]; then
  BEHIND=$(git rev-list --count HEAD..nas/main 2>/dev/null || echo "?")
  echo "SYNC BEHIND: Local data is ${BEHIND} commit(s) behind NAS."
  echo "  Run: cd ~/Documents/llm_brain && git pull nas main --rebase"
  exit 1
elif [ "${REMOTE}" = "${BASE}" ]; then
  AHEAD=$(git rev-list --count nas/main..HEAD 2>/dev/null || echo "?")
  echo "SYNC AHEAD: Local data has ${AHEAD} unpushed commit(s)."
  echo "  Run: cd ~/Documents/llm_brain && git push nas main"
  exit 1
else
  echo "SYNC DIVERGED: Local and NAS have diverged. Manual reconciliation needed."
  echo "  Run: cd ~/Documents/llm_brain && git status"
  exit 1
fi
