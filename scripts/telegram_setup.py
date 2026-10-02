#!/usr/bin/env python3
"""
One-shot setup: waits for you to send any message to your bot,
then saves the chat_id to telegram.json.

Usage:
    python scripts/telegram_setup.py
"""

import json
import sys
from pathlib import Path

import requests
import yaml

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"


def main():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    tg_path = Path(cfg["telegram"]["config_file"]).expanduser()

    with open(tg_path) as f:
        tg = json.load(f)

    token = tg.get("token")
    if not token:
        print("ERROR: No token in telegram.json")
        sys.exit(1)

    if tg.get("chat_id"):
        print(f"chat_id already set: {tg['chat_id']}")
        sys.exit(0)

    print("Open Telegram and send any message to your bot now...")
    print("Waiting (press Ctrl+C to cancel)...")

    offset = 0
    while True:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/getUpdates",
                json={"offset": offset, "timeout": 30},
                timeout=35,
            )
            updates = resp.json().get("result", [])
        except requests.exceptions.RequestException as e:
            print(f"Network error: {e}. Retrying...")
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue
            chat_id = msg["chat"]["id"]
            tg["chat_id"] = chat_id
            with open(tg_path, "w") as f:
                json.dump(tg, f, indent=2)
            print(f"chat_id saved: {chat_id}")
            print("Setup complete. You can now use telegram_notify.py and telegram_bot.py.")
            sys.exit(0)


if __name__ == "__main__":
    main()
