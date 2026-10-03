#!/usr/bin/env python3
"""
Shared Google OAuth handling for llm_brain scripts.

Calendar and Gmail share one token (google_token.json), so both must always
request the same scope set — otherwise whichever script re-auths last silently
drops the other's scope. Drive uses its own token (docs_helper.py).

Re-authorize everything interactively:
    .venv/bin/python scripts/google_auth.py            # calendar + gmail token
    .venv/bin/python scripts/google_auth.py --drive    # drive token
    .venv/bin/python scripts/google_auth.py --check    # report status, no browser
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"

# The shared calendar + gmail token. Keep this list in one place.
SHARED_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


# Background jobs (launchd) set this so an expired token fails fast instead of
# opening a browser on an unattended machine.
HEADLESS = os.environ.get("LLM_BRAIN_HEADLESS") == "1"


# OAuth apps left in "Testing" mode get refresh tokens that Google revokes 7 days after consent.
TESTING_TOKEN_DAYS = 7


def meta_path(token_path: Path) -> Path:
    return token_path.with_name(token_path.stem + ".meta.json")


def token_expiry(token_path: Path) -> datetime | None:
    """When the current sign-in stops working (Testing-mode apps), from the recorded sign-in time."""
    meta = meta_path(token_path)
    if not meta.exists():
        return None
    issued = datetime.fromisoformat(json.loads(meta.read_text())["issued"])
    return issued + timedelta(days=TESTING_TOKEN_DAYS)


class AuthRequired(RuntimeError):
    """Raised when a browser re-auth is needed but interactive=False."""


def _has_scopes(creds: Credentials, required: list[str]) -> bool:
    return set(required) <= set(creds.scopes or [])


def get_credentials(
    creds_path: Path,
    token_path: Path,
    scopes: list[str] = SHARED_SCOPES,
    interactive: bool | None = None,
) -> Credentials:
    """
    Load, refresh, or (re)create OAuth credentials.

    Falls back to the browser flow when the token is missing, lacks a scope, or
    its refresh token has been revoked/expired (Google expires refresh tokens
    after 7 days for OAuth apps left in "Testing" mode). With interactive=False
    (launchd jobs, Telegram notifier) it raises AuthRequired instead of opening
    a browser nobody is looking at.
    """
    if interactive is None:
        interactive = not HEADLESS
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path))

    if creds and creds.valid and _has_scopes(creds, scopes):
        return creds

    if creds and creds.refresh_token and _has_scopes(creds, scopes):
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            return creds
        except RefreshError as e:
            print(f"Token refresh failed ({e}); re-authorization needed.", file=sys.stderr)

    if not interactive:
        raise AuthRequired(
            f"Google token at {token_path} needs re-authorization. "
            "Run: .venv/bin/python scripts/google_auth.py"
        )

    return browser_signin(creds_path, token_path, scopes)


def browser_signin(creds_path: Path, token_path: Path, scopes: list[str]) -> Credentials:
    """Run Google's consent flow; only on success replace the token and record the sign-in time."""
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
    creds = flow.run_local_server(port=0, timeout_seconds=600)
    token_path.write_text(creds.to_json())
    meta_path(token_path).write_text(json.dumps({"issued": datetime.now().isoformat(timespec="minutes")}))
    return creds


def _paths(cfg: dict, drive: bool) -> tuple[Path, Path]:
    section = cfg["gdrive"] if drive else cfg["gcal"]
    token_key = "drive_token" if drive else "token"
    return Path(section["credentials"]).expanduser(), Path(section[token_key]).expanduser()


def main():
    parser = argparse.ArgumentParser(description="Authorize llm_brain Google access")
    parser.add_argument("--drive", action="store_true", help="Authorize the Drive token instead")
    parser.add_argument("--check", action="store_true", help="Report token status without opening a browser")
    parser.add_argument("--renew", action="store_true",
                        help="Always run the browser sign-in (fresh 7-day token) and report the result on Telegram")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    creds_path, token_path = _paths(cfg, args.drive)
    scopes = DRIVE_SCOPES if args.drive else SHARED_SCOPES
    label = "Drive" if args.drive else "Calendar + Gmail"

    if args.renew:
        from telegram_notify import send
        try:
            browser_signin(creds_path, token_path, scopes)  # old token stays until this succeeds
        except Exception as e:
            send(f"❌ {label} sign-in failed: {e}")
            sys.exit(1)
        until = token_expiry(token_path)
        send(f"✅ {label} login renewed" + (f" — valid until {until:%a %d %b %H:%M}." if until else "."))
        print(f"{label}: renewed")
        return

    try:
        get_credentials(creds_path, token_path, scopes, interactive=not args.check)
    except AuthRequired as e:
        print(f"{label}: NEEDS RE-AUTH — {e}")
        sys.exit(1)
    print(f"{label}: OK ({token_path})")


if __name__ == "__main__":
    main()
