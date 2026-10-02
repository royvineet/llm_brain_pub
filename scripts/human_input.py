#!/usr/bin/env python3
"""
Ask the user for something (a 2FA/OTP code, a captcha answer) over Telegram
and wait for the reply — used by web automation (web.py).

telegram_bot.py is the only process reading Telegram updates, so the handoff
goes through files in <data>/web/inbox/:
  1. request() writes <id>.request.json and sends the prompt as a Telegram message
  2. the bot sees a pending request; the user's next reply to that prompt — or any
     message that is just a 4–8 digit code — is written to <id>.response instead
     of going to Claude
  3. request() picks up the response file and returns the text

A request expires after `timeout` seconds; the prompt then says so.
"""

import json
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


def pending() -> dict | None:
    """The newest unexpired request, if any (called by the bot for every message)."""
    now = time.time()
    reqs = []
    for p in inbox().glob("*.request.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("expires", 0) > now:
            reqs.append(r)
    return max(reqs, key=lambda r: r["expires"], default=None)


def claims(req: dict, text: str, reply_to_id: int | None) -> bool:
    """Does this incoming Telegram message answer the pending request?"""
    return (reply_to_id is not None and reply_to_id == req.get("message_id")) or bool(CODE_RE.match(text))


def answer(req: dict, text: str):
    m = CODE_RE.match(text)
    value = re.sub(r"[\s-]", "", m.group(1)) if m else text.strip()
    (inbox() / f"{req['id']}.response").write_text(value)
    print(f"[{datetime.now():%H:%M}] answered input request {req['id']}", flush=True)
