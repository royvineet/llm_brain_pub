#!/usr/bin/env bash
# Install (or remove) the llm_brain background agents on macOS:
#   com.llmbrain.telegram-bot  — telegram_bot.py, kept alive
#   com.llmbrain.notify        — notify.py, every 15 min
#   com.llmbrain.sync          — scheduled_sync.sh (Calendar, Gmail, recurring, purge, reindex), every 30 min
#
# Personal jobs: if <data>/extensions/launchd.sh exists it is sourced after the
# above, and can call `write_plist LABEL /abs/path/script.py "<schedule xml>"`
# (helpers: weekdays_at HOUR MINUTE). Keeps personal automation out of the repo.
#
# Usage:
#   bash scripts/install_launchd.sh            # install / reinstall
#   bash scripts/install_launchd.sh --uninstall
#
# Logs: ~/Library/Logs/llm_brain/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${HOME}/Library/Logs/llm_brain"
DOMAIN="gui/$(id -u)"
DATA_DIR="${LLM_BRAIN_DATA:-${HOME}/Documents/llm_brain}"
EXTENSION_JOBS="${DATA_DIR}/extensions/launchd.sh"

# Remove every llm_brain agent (core and extension) before (re)installing.
shopt -s nullglob
for plist in "${AGENTS_DIR}"/com.llmbrain.*.plist; do
  label="$(basename "${plist}" .plist)"
  launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
  rm -f "${plist}"
  echo "Removed ${label}"
done

if [ "${1:-}" = "--uninstall" ]; then
  exit 0
fi

[ -x "${PYTHON}" ] || { echo "Error: ${PYTHON} not found — run scripts/startup.sh first." >&2; exit 1; }
CLAUDE_BIN="$(command -v claude || true)"
[ -n "${CLAUDE_BIN}" ] || { echo "Error: claude CLI not in PATH (the bot needs it)." >&2; exit 1; }
AGENT_PATH="$(dirname "${CLAUDE_BIN}"):/usr/local/bin:/usr/bin:/bin"

mkdir -p "${AGENTS_DIR}" "${LOG_DIR}"

write_plist() {  # label script schedule-xml — script is relative to scripts/ or an absolute path
  local interpreter="${PYTHON}" script="$2"
  [[ "${script}" == /* ]] || script="${REPO_ROOT}/scripts/${script}"
  [[ "${script}" == *.sh ]] && interpreter="/bin/bash"
  cat > "${AGENTS_DIR}/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$1</string>
    <key>ProgramArguments</key>
    <array>
        <string>${interpreter}</string>
        <string>${script}</string>
    </array>
    <key>WorkingDirectory</key><string>${REPO_ROOT}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>${AGENT_PATH}</string>
        <key>PYTHONWARNINGS</key><string>ignore</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
        <key>LLM_BRAIN_HEADLESS</key><string>1</string>
    </dict>
    $3
    <key>StandardOutPath</key><string>${LOG_DIR}/$1.log</string>
    <key>StandardErrorPath</key><string>${LOG_DIR}/$1.log</string>
</dict>
</plist>
EOF
  launchctl bootstrap "${DOMAIN}" "${AGENTS_DIR}/$1.plist"
  echo "Installed $1"
}

write_plist com.llmbrain.telegram-bot telegram_bot.py \
  "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>"
write_plist com.llmbrain.notify notify.py \
  "<key>RunAtLoad</key><true/><key>StartInterval</key><integer>900</integer>"
write_plist com.llmbrain.sync scheduled_sync.sh \
  "<key>RunAtLoad</key><true/><key>StartInterval</key><integer>1800</integer>"

weekdays_at() {  # HOUR MINUTE → StartCalendarInterval XML for Mon–Fri
  local xml="<key>StartCalendarInterval</key><array>"
  for d in 1 2 3 4 5; do
    xml+="<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>$1</integer><key>Minute</key><integer>$2</integer></dict>"
  done
  echo "${xml}</array>"
}

if [ -f "${EXTENSION_JOBS}" ]; then
  # shellcheck source=/dev/null
  source "${EXTENSION_JOBS}"
fi

echo "Logs: ${LOG_DIR}"
