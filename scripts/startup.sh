#!/usr/bin/env bash
# llm_brain startup script.
# - Pulls latest llm_brain project code from GitHub (origin/main)
# - Syncs ~/Documents/llm_brain with the NAS remote (pull if behind, push if ahead)
# - Rebuilds the index, purges todos, generates recurring tasks/events
# - Syncs Google Calendar and Gmail (shared OAuth token; browser re-auth if expired)

set -euo pipefail

LLM_BRAIN_DIR="${HOME}/Documents/llm_brain"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Detect OS ---
OS="$(uname -s)"
case "${OS}" in
  Darwin) OS_NAME="macOS" ;;
  Linux)
    if grep -qi ubuntu /etc/os-release 2>/dev/null; then
      OS_NAME="Ubuntu"
    else
      OS_NAME="Linux"
    fi
    ;;
  *) OS_NAME="${OS}" ;;
esac
echo "==> Detected OS: ${OS_NAME}"

# --- Check and install system dependencies ---
if [ "${OS_NAME}" = "Ubuntu" ]; then
  MISSING_APT=()
  python3 -m venv --help >/dev/null 2>&1 || MISSING_APT+=("python3-venv")
  if [ ${#MISSING_APT[@]} -gt 0 ]; then
    echo "==> Installing missing system packages: ${MISSING_APT[*]}"
    sudo apt-get install -y "${MISSING_APT[@]}"
  fi
fi
echo ""

SYSTEM_PYTHON="$(command -v python3 || true)"
if [ -z "${SYSTEM_PYTHON}" ]; then
  echo "Error: python3 not found in PATH." >&2
  exit 1
fi

# --- Set up virtual environment ---
VENV_DIR="${REPO_ROOT}/.venv"
if [ ! -d "${VENV_DIR}" ]; then
  echo "==> Creating virtual environment..."
  if ! "${SYSTEM_PYTHON}" -m venv "${VENV_DIR}"; then
    if [ "${OS_NAME}" = "Ubuntu" ]; then
      echo "Error: venv creation failed. Run: sudo apt install python3-venv" >&2
    else
      echo "Error: venv creation failed." >&2
    fi
    exit 1
  fi
fi
PYTHON="${VENV_DIR}/bin/python3"
echo "==> Installing/updating requirements..."
"${VENV_DIR}/bin/pip" install -q -r "${REPO_ROOT}/requirements.txt"
echo "Requirements up to date."
echo ""
echo "To activate the venv in your shell: source .venv/bin/activate"
echo ""

# --- Pull latest project code from GitHub ---
echo "==> Pulling latest llm_brain code from GitHub..."
cd "${REPO_ROOT}"
if ! git fetch origin --quiet 2>/dev/null; then
  echo "WARNING: Could not reach GitHub. Skipping code pull."
  echo ""
else
  LOCAL=$(git rev-parse @)
  REMOTE=$(git rev-parse "origin/main" 2>/dev/null || true)
  BASE=$(git merge-base @ "origin/main" 2>/dev/null || true)

  if [ -z "${REMOTE}" ]; then
    echo "INFO: Could not resolve origin/main. Skipping code pull."
  elif [ "${LOCAL}" = "${REMOTE}" ]; then
    echo "Code up to date with GitHub."
  elif [ "${LOCAL}" = "${BASE}" ]; then
    BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
    echo "Behind by ${BEHIND} commit(s) — pulling..."
    git pull origin main --rebase
    echo "Pull complete."
  elif [ "${REMOTE}" = "${BASE}" ]; then
    echo "INFO: Local code is ahead of GitHub. No pull needed."
  else
    echo "WARNING: Local code has diverged from GitHub. Skipping pull — resolve manually." >&2
    echo "  cd ${REPO_ROOT} && git status" >&2
  fi
fi
echo ""

if [ ! -d "${LLM_BRAIN_DIR}/.git" ]; then
  echo "Error: ${LLM_BRAIN_DIR} is not a git repository."
  echo "Run 'bash scripts/init.sh' first to set up the data directory." >&2
  exit 1
fi

cd "${LLM_BRAIN_DIR}"

# --- Sync with remote (if one is configured) ---
REMOTE_NAME=$(git remote 2>/dev/null | head -1 || true)

if [ -z "${REMOTE_NAME}" ]; then
  echo "INFO: No git remote configured in data directory — skipping sync."
  echo "      To back up your data, add a remote:"
  echo "        git -C \"${LLM_BRAIN_DIR}\" remote add origin <url>"
  echo ""
else
  echo "==> Syncing data with remote '${REMOTE_NAME}'..."
  if ! git fetch "${REMOTE_NAME}" --quiet 2>/dev/null; then
    echo "WARNING: Could not reach remote '${REMOTE_NAME}'. Skipping sync — data may be stale."
    echo ""
  else
    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse "@{u}" 2>/dev/null || true)
    BASE=$(git merge-base @ "@{u}" 2>/dev/null || true)

    if [ -z "${REMOTE}" ]; then
      echo "INFO: No upstream tracking branch set. Skipping sync."
      echo "      To track a remote branch: git -C \"${LLM_BRAIN_DIR}\" branch --set-upstream-to=${REMOTE_NAME}/main main"
    elif [ "${LOCAL}" = "${REMOTE}" ]; then
      echo "Sync OK: up to date with remote."
    elif [ "${LOCAL}" = "${BASE}" ]; then
      BEHIND=$(git rev-list --count HEAD.."${REMOTE_NAME}/main" 2>/dev/null || echo "?")
      echo "Behind by ${BEHIND} commit(s) — pulling..."
      git pull "${REMOTE_NAME}" main --rebase
      echo "Pull complete."
    elif [ "${REMOTE}" = "${BASE}" ]; then
      AHEAD=$(git rev-list --count "${REMOTE_NAME}/main"..HEAD 2>/dev/null || echo "?")
      echo "Ahead by ${AHEAD} commit(s) — pushing..."
      git push "${REMOTE_NAME}" main
      echo "Push complete."
    else
      echo "ERROR: Local data has diverged from remote. Resolve manually before continuing." >&2
      echo "  cd ${LLM_BRAIN_DIR} && git status" >&2
      exit 1
    fi
  fi
fi

echo ""
echo "==> Rebuilding index..."
"${PYTHON}" "${REPO_ROOT}/scripts/reindex.py"

echo ""
echo "==> Purging completed todos..."
"${PYTHON}" "${REPO_ROOT}/scripts/purge_todos.py"

echo ""
echo "==> Generating recurring tasks..."
"${PYTHON}" "${REPO_ROOT}/scripts/generate_recurring_tasks.py"

echo ""
echo "==> Generating recurring events..."
"${PYTHON}" "${REPO_ROOT}/scripts/generate_recurring_events.py"

echo ""
echo "==> Syncing Google Calendar + Gmail..."
GCAL_CREDS="${LLM_BRAIN_DIR}/credentials.json"
if [ ! -f "${GCAL_CREDS}" ]; then
  echo "INFO: credentials.json not found — skipping Google sync. See README for setup."
else
  # One shared token for Calendar + Gmail. Opens a browser if re-auth is needed.
  if ! PYTHONWARNINGS=ignore "${PYTHON}" "${REPO_ROOT}/scripts/google_auth.py"; then
    echo "WARNING: Google auth failed — skipping Calendar and Gmail sync." >&2
  else
    PYTHONWARNINGS=ignore "${PYTHON}" "${REPO_ROOT}/scripts/sync_gcal.py" \
      || echo "WARNING: Google Calendar sync failed — see output above." >&2
    echo ""
    PYTHONWARNINGS=ignore "${PYTHON}" "${REPO_ROOT}/scripts/sync_gmail.py" \
      || echo "WARNING: Gmail sync failed — see output above." >&2
  fi
fi
