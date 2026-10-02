#!/usr/bin/env python3
"""
Send a Telegram message to the configured chat.

Usage (CLI):
    python scripts/telegram_notify.py "Hello from llm_brain"

Usage (module):
    from telegram_notify import send
    send("Task due in 30 minutes: Buy groceries")
    send("<b>Due today</b>", parse_mode="HTML",
         buttons=[[("✓ Done", "done:42"), ("Snooze 1d", "snooze:42:1")]])

Buttons are handled by telegram_bot.py (callback_query), so it must be running
for them to do anything.

Config is read from ~/Documents/llm_brain/telegram.json:
    {
        "token": "YOUR_BOT_TOKEN",
        "chat_id": 123456789
    }

Run telegram_setup.py once and send any message to the bot to populate chat_id.
"""

import json
import sys
from pathlib import Path

import requests
import yaml

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"


def load_telegram_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    tg_path = Path(cfg["telegram"]["config_file"]).expanduser()
    if not tg_path.exists():
        raise FileNotFoundError(
            f"Telegram config not found at {tg_path}.\n"
            "Create it with: {\"token\": \"YOUR_BOT_TOKEN\", \"chat_id\": null}"
        )
    with open(tg_path) as f:
        return json.load(f)


def _creds() -> tuple[str, int] | None:
    tg = load_telegram_config()
    if not tg.get("token"):
        print("ERROR: No bot token in telegram.json", file=sys.stderr)
        return None
    if not tg.get("chat_id"):
        print("ERROR: chat_id not set in telegram.json. Run telegram_setup.py first.", file=sys.stderr)
        return None
    return tg["token"], tg["chat_id"]


def send(
    message: str,
    buttons: list[list[tuple[str, str]]] | None = None,
    parse_mode: str | None = None,
    force_reply: bool = False,
) -> int | bool:
    """
    Send a message to the configured Telegram chat. Returns the Telegram
    message_id on success (truthy), False on failure.

    buttons: rows of (label, callback_data) pairs rendered as an inline keyboard.
    force_reply: open the reply box on the user's phone (for prompts that need an answer).
    """
    creds = _creds()
    if not creds:
        return False
    token, chat_id = creds

    payload: dict = {"chat_id": chat_id, "text": message[:4096], "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if force_reply:
        payload["reply_markup"] = {"force_reply": True}
    elif buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row] for row in buttons
            ]
        }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json=payload, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Telegram unreachable: {e}", file=sys.stderr)
        return False

    if not resp.ok:
        print(f"ERROR: Telegram API error: {resp.status_code} {resp.text}", file=sys.stderr)
        return False
    return resp.json()["result"]["message_id"]


def send_photo(path: Path, caption: str = "") -> bool:
    """Send an image (e.g. a failure screenshot) to the configured chat."""
    creds = _creds()
    if not creds:
        return False
    token, chat_id = creds
    try:
        with open(path, "rb") as f:
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                                 data={"chat_id": chat_id, "caption": caption[:1024]},
                                 files={"photo": f}, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Telegram unreachable: {e}", file=sys.stderr)
        return False
    return resp.ok


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/telegram_notify.py \"Your message here\"")
        sys.exit(1)
    message = " ".join(sys.argv[1:])
    success = send(message)
    sys.exit(0 if success else 1)
