#!/usr/bin/env python3
"""
Ask the user for something (a 2FA/OTP code, a captcha answer) over Telegram
and wait for the reply — used by web automation (web.py).

telegram_bot.py is the only process reading Telegram updates, so the handoff
goes through files in <data>/web/inbox/:
  1. request() writes <id>.request.json and sends the prompt as a Telegram message
  2. the bot matches the user's message to a live request — a reply to that
     prompt, or else (for a message that is just a 4–8 digit code) the newest
     live request — and writes <id>.response instead of passing it to Claude
  3. request() picks up the response file and returns the text

A request expires after `timeout` seconds; the prompt then says so. Requests
whose process has died (killed run) are ignored and cleaned up, so a stale
prompt can never swallow a code meant for a newer one.
"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from telegram_notify import send

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"
CODE_RE = re.compile(r"^\s*(\d[\d\s-]{2,10}\d)\s*$")


def inbox() -> Path:
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    d = Path(cfg["storage"]["tasks"]).expanduser().parent / "web" / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def request(prompt: str, timeout: int = 300) -> str | None:
    """Send `prompt` to Telegram and block until the user replies (or timeout → None)."""
    rid = uuid.uuid4().hex[:8]
    req_path, resp_path = inbox() / f"{rid}.request.json", inbox() / f"{rid}.response"
    msg_id = send(f"🔐 {prompt}\n\nReply with the code (within {timeout // 60} min).", force_reply=True)
    req_path.write_text(json.dumps({"id": rid, "prompt": prompt, "message_id": msg_id or None,
                                    "pid": os.getpid(), "created": time.time(),
                                    "expires": time.time() + timeout}))
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if resp_path.exists():
                return resp_path.read_text().strip()
            time.sleep(1)
        send(f"⌛ Timed out waiting for: {prompt}")
        return None
    finally:
        req_path.unlink(missing_ok=True)
        resp_path.unlink(missing_ok=True)


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def live_requests() -> list[dict]:
    """Unexpired requests whose asking process is still running, newest first. Deletes the rest."""
    now, live = time.time(), []
    for p in inbox().glob("*.request.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("expires", 0) > now and _alive(r.get("pid")):
            live.append(r)
        else:
            p.unlink(missing_ok=True)
            (inbox() / f"{r.get('id')}.response").unlink(missing_ok=True)
    return sorted(live, key=lambda r: r.get("created", 0), reverse=True)


def match(text: str, reply_to_id: int | None) -> tuple[dict | None, bool]:
    """
    Which live request does this Telegram message answer?
    Returns (request, replied_to_dead_prompt). A reply to a prompt goes to that prompt only;
    otherwise a bare code goes to the newest live request.
    """
    live = live_requests()
    if reply_to_id is not None:
        for r in live:
            if r.get("message_id") == reply_to_id:
                return r, False
        if CODE_RE.match(text):
            # Replied to a prompt that is no longer waiting (expired / run killed). If exactly one
            # prompt is live, that's clearly the one meant; otherwise don't guess.
            return (live[0], False) if len(live) == 1 else (None, True)
        return None, False
    if CODE_RE.match(text) and live:
        return live[0], False
    return None, False


def answer(req: dict, text: str):
    m = CODE_RE.match(text)
    value = re.sub(r"[\s-]", "", m.group(1)) if m else text.strip()
    (inbox() / f"{req['id']}.response").write_text(value)
    print(f"[{datetime.now():%H:%M}] answered input request {req['id']}", flush=True)
