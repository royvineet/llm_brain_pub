#!/usr/bin/env bash
# Unattended refresh, run every 30 min by launchd (com.llmbrain.sync):
# Google Calendar + Gmail sync, recurring task/event generation, todo purge,
# reindex. Every step is idempotent. No git — committing/pushing data stays
# manual (/sync or startup.sh).
#
# An expired Google token makes the sync steps fail fast (LLM_BRAIN_HEADLESS=1);
# notify.py's morning digest then warns on Telegram.

set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python"
export LLM_BRAIN_HEADLESS=1 PYTHONWARNINGS=ignore

run() {
  echo "[$(date '+%F %T')] $1"
  "${PY}" "${REPO_ROOT}/scripts/$1" > /tmp/llmbrain_step.log 2>&1 \
    || { echo "  FAILED:"; tail -5 /tmp/llmbrain_step.log | sed 's/^/    /'; }
}

run sync_gcal.py
run sync_gmail.py
run generate_recurring_tasks.py
run generate_recurring_events.py
run purge_todos.py
run reindex.py
