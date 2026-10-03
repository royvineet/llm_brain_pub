#!/usr/bin/env python3
"""
Telegram bot — relay to Claude Code, plus one-tap task actions.

- Text messages are passed to `claude -p` in the llm_brain repo. Follow-ups
  within SESSION_IDLE_MIN resume the same Claude session, so "move it to
  Friday" works after "what's due today?". Send /new to start fresh.
- When a web.py run is waiting for a 2FA code (human_input.py), a reply to its
  prompt — or any message that's just a code — goes to that run, not to Claude.
- Inline buttons on notifications from notify.py (done / snooze / cancel /
  resched) update tasks.yaml directly, without a Claude round-trip. Repeated
  snoozing (SNOOZE_LIMIT) prompts to drop or reschedule instead.

Only messages from the chat_id in telegram.json are accepted.

Usage:
    python scripts/telegram_bot.py        # usually run by launchd (install_launchd.sh)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yaml

import brain_context
import human_input
import speech
from task import locked_tasks

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"
REPO_ROOT = Path(__file__).parent.parent
POLL_TIMEOUT = 30
CLAUDE_TIMEOUT_S = 600
SESSION_IDLE_MIN = 60

# Headless Claude can't answer permission prompts, so pre-approve what the
# assistant needs: edits inside the data dir and the repo's own scripts.
CLAUDE_ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(.venv/bin/python scripts/*)",
    "Bash(date*)",
    "Skill",
]
CLAUDE_MODEL = "opus"  # with the snapshot most replies are 1 turn, so speed ≈ sonnet and quality is better
TELEGRAM_PROMPT = """You are replying through Telegram on the user's phone.

Speed matters — every tool call adds seconds:
- Each message starts with a [SNAPSHOT] (time, day type, open tasks, next 7 days of events,
  recurring meds/cards, latest inbox). It is current. Answer from it directly; don't read
  tasks.yaml / events.yaml / index.yaml / calendar.md for anything the snapshot covers.
- Read other files only for what it lacks: task descriptions, done history, journal,
  profiles (read profiles/directives.md before scheduling anything non-trivial), emails' bodies.
- Change tasks with ONE command, never by editing tasks.yaml:
  .venv/bin/python scripts/task.py add --title "..." --due YYYY-MM-DD --priority low|medium|high
      --category personal|work --tags personal,errands [--actionable business_hours|anytime|weekend|office]
      [--reminder [HH:MM]  ← ONLY if the user asked to be reminded; bare = configured default time]
      [--remind critical|important|routine --remind-days-before 5,2 --lapse] [--description "..."]
  .venv/bin/python scripts/task.py update ID [same flags] [--clear due,remind_at]
  .venv/bin/python scripts/task.py done|cancel ID [ID ...]
  Recurring things (meds, bills) go in recurring_tasks.yaml instead (see CLAUDE.md).
- Batch independent tool calls in one turn.

Web automation (logins to sites): run `.venv/bin/python scripts/web.py run SITE ACTION`
(`web.py list` shows what exists). It detaches immediately; tell the user the 2FA prompt and the result will
arrive here on Telegram — don't wait for it.
If a run failed, read ~/Library/Logs/llm_brain/web.log (and the latest web-<site>-*.txt outline) to explain why.
Background jobs (web.py runs, scheduled-job scripts): start each one ONCE with the documented command. Never start
alternatives in parallel or retry while one may still be running (check ~/Library/Logs/llm_brain/web.log first) —
parallel runs each ask for their own 2FA code and confuse the handoff. If a command is denied, tell the user;
don't look for another way to launch it (no nohup, &, launchctl).

Replies: short plain text, bullets with '•', no markdown tables or headings. Confirm changes
in one line, mentioning any reminder settings you chose. If you need clarification, ask one question."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_telegram_config(cfg: dict) -> dict:
    tg_path = Path(cfg["telegram"]["config_file"]).expanduser()
    if not tg_path.exists():
        raise FileNotFoundError(f"Telegram config not found at {tg_path}.")
    with open(tg_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def api(token: str, method: str, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=kwargs, timeout=POLL_TIMEOUT + 30)
    resp.raise_for_status()
    return resp.json()


def send_message(token: str, chat_id: int, text: str):
    # Telegram message limit is 4096 chars
    for i in range(0, max(1, len(text)), 4096):
        api(token, "sendMessage", chat_id=chat_id, text=text[i:i+4096])


def get_updates(token: str, offset: int) -> list:
    result = api(token, "getUpdates", offset=offset, timeout=POLL_TIMEOUT,
                 allowed_updates=["message", "edited_message", "callback_query"])
    return result.get("result", [])


# ---------------------------------------------------------------------------
# Claude relay
# ---------------------------------------------------------------------------

def extension_tools(data_dir: Path) -> list[str]:
    """Personal scripts live in <data>/extensions/scripts (outside the repo) — allow running them too."""
    ext = data_dir / "extensions" / "scripts"
    home_rel = "~/" + str(ext.relative_to(Path.home())) if ext.is_relative_to(Path.home()) else str(ext)
    return [f"Bash(.venv/bin/python {ext}/*)", f"Bash(.venv/bin/python {home_rel}/*)"] if ext.is_dir() else []


class ClaudeSession:
    """Tracks the current Claude session so follow-up messages keep context."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.session_id: str | None = None
        self.last_used = 0.0

    def reset(self):
        self.session_id = None

    def ask(self, message: str) -> str:
        try:
            context = brain_context.snapshot()
        except Exception as e:  # a broken snapshot shouldn't take the bot down
            context = f"(snapshot unavailable: {e})"
        cmd = [
            "claude", "-p", f"[SNAPSHOT]\n{context}\n[/SNAPSHOT]\n\n{message}",
            "--output-format", "json",
            "--model", CLAUDE_MODEL,
            "--permission-mode", "acceptEdits",
            "--add-dir", str(self.data_dir),
            "--add-dir", str(Path.home() / "Library" / "Logs" / "llm_brain"),  # web.py logs + failure screenshots
            "--allowedTools", *CLAUDE_ALLOWED_TOOLS, *extension_tools(self.data_dir),
            "--append-system-prompt", TELEGRAM_PROMPT,
        ]
        if self.session_id and time.time() - self.last_used < SESSION_IDLE_MIN * 60:
            cmd += ["--resume", self.session_id]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT,
                                    stdin=subprocess.DEVNULL,  # claude -p appends piped stdin to the prompt
                                    timeout=CLAUDE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.reset()
            return f"Claude timed out after {CLAUDE_TIMEOUT_S // 60} min."

        try:
            out = json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip() or result.stderr.strip() or "Error: no response from Claude."

        self.session_id = out.get("session_id") or self.session_id
        self.last_used = time.time()
        return (out.get("result") or "").strip() or "No response."


# ---------------------------------------------------------------------------
# Task button actions (callback_data from notify.py)
# ---------------------------------------------------------------------------

SNOOZE_LIMIT = 3


def apply_task_action(tasks_path: Path, data: str) -> tuple[str, list | None]:
    """
    Handle 'done:<id>', 'cancel:<id>', 'snooze:<id>:<days>', 'resched:<id>:<days>'.
    Returns (status line, follow-up buttons or None). After SNOOZE_LIMIT snoozes the
    follow-up asks to drop or properly reschedule instead of snoozing forever.
    """
    parts = data.split(":")
    action, task_id = parts[0], int(parts[1])

    with locked_tasks(tasks_path) as doc:
        return _apply(doc, action, task_id, parts)


def _apply(doc: dict, action: str, task_id: int, parts: list[str]) -> tuple[str, list | None]:
    task = next((t for t in doc.get("tasks", []) if t.get("id") == task_id), None)
    if not task:
        return f"Task #{task_id} not found.", None
    if task.get("status") not in ("pending", "in_progress"):
        return f"#{task_id} is already {task.get('status')}.", None

    today = date.today()
    follow_up = None
    if action == "done":
        task["status"] = "done"
        task["completed_at"] = today.isoformat()
        msg = f"✓ Done: #{task_id} {task['title']}"
    elif action == "cancel":
        task["status"] = "cancelled"
        task["completed_at"] = today.isoformat()
        msg = f"✗ {'Skipped' if task.get('lapse') else 'Dropped'}: #{task_id} {task['title']}"
    elif action in ("snooze", "resched"):
        new_due = today + timedelta(days=int(parts[2]))
        task["due_date"] = new_due.isoformat()
        msg = f"⏭ #{task_id} moved to {new_due.strftime('%a %d %b')}"
        if action == "resched":
            task.pop("snooze_count", None)
        else:
            task["snooze_count"] = task.get("snooze_count", 0) + 1
            if task["snooze_count"] >= SNOOZE_LIMIT:
                follow_up = [[("✗ Drop", f"cancel:{task_id}"), ("+1 week", f"resched:{task_id}:7"),
                              ("+1 month", f"resched:{task_id}:30")]]
    else:
        return f"Unknown action: {action}", None

    return msg, follow_up


def transcribe_voice(token: str, chat_id: int, voice: dict) -> str | None:
    """Download a Telegram voice/audio note, transcribe it locally, echo the transcript. None on failure."""
    if problem := speech.available():
        send_message(token, chat_id, f"🎙 Voice notes aren't set up on the Mac: {problem}")
        return None
    api(token, "sendChatAction", chat_id=chat_id, action="typing")
    info = api(token, "getFile", file_id=voice["file_id"])["result"]
    audio = requests.get(f"https://api.telegram.org/file/bot{token}/{info['file_path']}", timeout=60)
    audio.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=Path(info["file_path"]).suffix or ".oga") as f:
        f.write(audio.content)
        f.flush()
        try:
            text = speech.transcribe(Path(f.name))
        except Exception as e:
            send_message(token, chat_id, f"🎙 Couldn't transcribe that: {e}")
            return None
    if not text:
        send_message(token, chat_id, "🎙 I couldn't make out any words — try again?")
        return None
    send_message(token, chat_id, f"🎙 “{text}”")
    return text


def start_google_renew() -> str:
    """Open Google's sign-in on the Mac (detached); google_auth.py reports the result on Telegram."""
    env = {**os.environ, "LLM_BRAIN_HEADLESS": "0", "PYTHONWARNINGS": "ignore"}
    log = open(Path.home() / "Library/Logs/llm_brain/google_auth.log", "a")
    subprocess.Popen([sys.executable, str(REPO_ROOT / "scripts/google_auth.py"), "--renew"], env=env,
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=REPO_ROOT)
    return "Sign-in opened in a browser on the Mac — finish it there (10 min)."


def handle_callback(token: str, cb: dict, tasks_path: Path):
    data = cb.get("data", "")
    if data == "gauth":
        api(token, "answerCallbackQuery", callback_query_id=cb["id"], text=start_google_renew())
        return
    follow_up = None
    try:
        status, follow_up = apply_task_action(tasks_path, data)
    except Exception as e:  # never let a bad button kill the bot
        status = f"Error: {e}"
    api(token, "answerCallbackQuery", callback_query_id=cb["id"], text=status[:200])

    msg = cb.get("message")
    if msg:
        # Drop only this task's row so the other tasks in a batched message stay tappable.
        task_id = data.split(":")[1] if ":" in data else ""
        rows = (msg.get("reply_markup") or {}).get("inline_keyboard", [])
        keep = [r for r in rows if not any(b.get("callback_data", "").split(":")[1:2] == [task_id] for b in r)]
        payload = {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                   "text": f"{msg.get('text', '')}\n{status}"}
        if keep:
            payload["reply_markup"] = {"inline_keyboard": keep}
        api(token, "editMessageText", **payload)
    if follow_up:
        api(token, "sendMessage", chat_id=cb["message"]["chat"]["id"],
            text=f"You've snoozed this {SNOOZE_LIMIT} times. Drop it, or give it a real date?",
            reply_markup={"inline_keyboard": [[{"text": l, "callback_data": d} for l, d in row]
                                              for row in follow_up]})
    print(f"[{datetime.now():%H:%M}] button: {status}", flush=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()
    tg = load_telegram_config(cfg)
    token = tg.get("token")
    chat_id = tg.get("chat_id")
    if not token or not chat_id:
        print("ERROR: token/chat_id missing in telegram.json — run telegram_setup.py", file=sys.stderr)
        sys.exit(1)

    tasks_path = Path(cfg["storage"]["tasks"]).expanduser()
    claude = ClaudeSession(tasks_path.parent)
    offset = 0

    if speech.available() is None:  # load the speech model up front so the first voice note is quick
        speech.model()
    print("Bot started. Relaying messages to Claude Code...", flush=True)

    while True:
        try:
            updates = get_updates(token, offset)
        except requests.exceptions.RequestException as e:
            print(f"Network error: {e}. Retrying in 5s...", flush=True)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                cb = update.get("callback_query")
                if cb:
                    if cb.get("from", {}).get("id") == chat_id or cb.get("message", {}).get("chat", {}).get("id") == chat_id:
                        handle_callback(token, cb, tasks_path)
                    continue

                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                if msg["chat"]["id"] != chat_id:
                    print(f"Ignored message from unknown chat_id: {msg['chat']['id']}", flush=True)
                    continue

                text = msg.get("text", "").strip()
                voice = msg.get("voice") or msg.get("audio") or msg.get("video_note")
                if not text and voice:
                    text = transcribe_voice(token, chat_id, voice)
                if not text:
                    continue
                # A web automation run waiting for a 2FA code gets it first.
                req, dead = human_input.match(text, (msg.get("reply_to_message") or {}).get("message_id"))
                if req:
                    human_input.answer(req, text)
                    site = req.get("prompt", "").split(":", 1)[0]
                    send_message(token, chat_id, f"🔑 Got it — sent to {site}.")
                    continue
                if dead:
                    send_message(token, chat_id, "That prompt is no longer waiting (expired or the run was "
                                                 "stopped). Wait for a fresh 🔐 prompt and reply to that one.")
                    continue

                if text == "/start":
                    send_message(token, chat_id, "llm_brain bot ready. /new starts a fresh conversation.")
                    continue
                if text == "/new":
                    claude.reset()
                    send_message(token, chat_id, "Started a fresh conversation.")
                    continue

                if voice:  # tell Claude the text came from speech recognition
                    text = ("[Voice message, transcribed on-device — may contain recognition errors. If a name, "
                            "date or number looks misheard, check it against tasks/profiles or ask.]\n" + text)
                print(f"→ Claude: {text}", flush=True)
                api(token, "sendChatAction", chat_id=chat_id, action="typing")
                # Replying to a notification ("done", "move to Sat") only makes sense
                # with the quoted message — Claude never saw notify.py's sends.
                quoted = (msg.get("reply_to_message") or {}).get("text", "")
                if quoted:
                    text = f'[Replying to this message you sent earlier:\n"""\n{quoted}\n"""]\n\n{text}'
                reply = claude.ask(text)
                print(f"← Reply: {reply[:80]}...", flush=True)
                send_message(token, chat_id, reply)
            except requests.exceptions.RequestException as e:
                print(f"Telegram error while handling update: {e}", flush=True)


if __name__ == "__main__":
    main()
