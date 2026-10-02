#!/usr/bin/env python3
"""
Browser automation for llm_brain: per-site scripted flows ("recipes") driving
a real Chromium with a persistent profile per site. Recipes are loaded from
scripts/sites/ and from <data>/extensions/sites/ — keep personal ones (your
banks, brokers) in the extensions folder so the repo stays shareable.

- Credentials come from the macOS Keychain (service "llm_brain:<site>").
- 2FA / OTP codes are requested over Telegram (human_input.py) and the run
  waits for your reply.
- Runs detach by default, so a Telegram-triggered run never blocks the bot
  (which has to stay free to receive your OTP reply). Results arrive on
  Telegram: ✅ summary, or ❌ error + screenshot.
- One run per site at a time (lock); logs and failure screenshots in
  ~/Library/Logs/llm_brain/.

Usage:
    python scripts/web.py list
    python scripts/web.py creds set SITE       # prompts for username + password — run in a Terminal
    python scripts/web.py creds check SITE
    python scripts/web.py run SITE ACTION [--foreground] [--headless]
    python scripts/web.py open SITE [URL]     # plain browser on the site's profile, for one-time manual sign-in
"""

import argparse
import fcntl
import json
import getpass
import importlib
import os
import pkgutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import keyring
import yaml

import human_input
import sites
from telegram_notify import send, send_photo

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"
LOG_DIR = Path.home() / "Library" / "Logs" / "llm_brain"


@dataclass
class Ctx:
    """What a site recipe gets besides the page."""
    site: str
    username: str
    password: str
    ask: Callable[[str], str | None]    # ask the user over Telegram, e.g. for an OTP
    log: Callable[[str], None]
    data_dir: Path
    secrets: dict                       # site's EXTRA_SECRETS from the Keychain (missing ones absent)


def data_dir() -> Path:
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    return Path(cfg["storage"]["tasks"]).expanduser().parent


def site_modules() -> dict:
    ext = data_dir() / "extensions" / "sites"
    if ext.is_dir() and str(ext) not in sites.__path__:
        sites.__path__.append(str(ext))  # private recipes live outside the repo
    return {m.name: importlib.import_module(f"sites.{m.name}") for m in pkgutil.iter_modules(sites.__path__)}


def keychain_service(site: str) -> str:
    return f"llm_brain:{site}"


def extra_secrets(site: str) -> list[str]:
    return list(getattr(site_modules()[site], "EXTRA_SECRETS", []))


def needs_password(site: str) -> bool:
    """Sites that sign in via Google (session in the profile) store only the account email."""
    return getattr(site_modules()[site], "NEEDS_PASSWORD", True)


def get_creds(site: str) -> tuple[str, str]:
    user = keyring.get_password(keychain_service(site), "username")
    pw = keyring.get_password(keychain_service(site), "password") or ""
    if not user or (needs_password(site) and not pw):
        sys.exit(f"No credentials for {site}. In a Terminal on this machine run:\n"
                 f"  cd ~/repo/llm_brain && .venv/bin/python scripts/web.py creds set {site}")
    return user, pw


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def profile_in_use(profile: Path) -> bool:
    """True if a live Chromium (e.g. a `web.py open` window) holds this profile."""
    try:
        pid = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[1])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


def result_line(ok: bool, summary: str):
    """Machine-readable outcome for orchestrators (e.g. a scheduled extension job) reading a --foreground run's output."""
    print("RESULT " + json.dumps({"ok": ok, "summary": summary}), flush=True)


def run(site: str, action: str, headless: bool, quiet: bool = False):
    """Run one recipe action. Every outcome — including setup problems — is reported on Telegram,
    since detached runs have no one watching their output."""
    def fail(msg: str):
        log(f"FAILED: {msg}")
        result_line(False, msg)
        send(f"❌ {site} · {action}: {msg}"[:1000])
        sys.exit(1)

    mod = site_modules()[site]
    fn = mod.ACTIONS[action]
    try:
        user, pw = get_creds(site)
    except SystemExit as e:
        fail(str(e))
    profile = data_dir() / "web" / "profiles" / site
    profile.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if profile_in_use(profile):
        fail(f"the {site} browser window (from `web.py open {site}`) is still open. "
             "Quit it (Cmd+Q or Dock icon → Quit) and ask again.")

    with open(profile.parent / f"{site}.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            send(f"⏳ {site} is already running a job — try again in a minute.")
            sys.exit(1)

        from playwright._impl._errors import TargetClosedError  # not re-exported by sync_api
        from playwright.sync_api import sync_playwright
        otp_timeout = int(os.environ.get("LLM_BRAIN_OTP_TIMEOUT", "300"))
        ctx = Ctx(site, user, pw, ask=lambda prompt: human_input.request(f"{site}: {prompt}", timeout=otp_timeout),
                  log=log, data_dir=data_dir(),
                  secrets={k: v for k in extra_secrets(site)
                           if (v := keyring.get_password(keychain_service(site), k))})
        for attempt in (1, 2):
            with sync_playwright() as p:
                # Drop the obvious automation markers — some sites 403 a stock automated browser.
                try:
                    browser = p.chromium.launch_persistent_context(
                        str(profile), headless=headless, accept_downloads=True,
                        # No locale/timezone_id override (the system's is used); the timezone
                        # override made Chromium segfault while a site built an in-page XLSX export.
                        viewport={"width": 1366, "height": 900},
                        args=["--disable-blink-features=AutomationControlled"],
                        ignore_default_args=["--enable-automation"],
                    )
                except Exception as e:
                    fail(f"couldn't start the browser: {str(e).splitlines()[0]}")
                page = browser.pages[0] if browser.pages else browser.new_page()
                log(f"start {site} {action}" + (" (retry)" if attempt == 2 else ""))
                try:
                    summary = fn(page, ctx)
                    log(f"done: {summary}")
                    result_line(True, summary)
                    if not quiet:
                        send(f"✅ {site} · {action}\n{summary}")
                    return
                except TargetClosedError as e:
                    # Chromium occasionally crashes mid-run (seen on an in-page export); a fresh browser
                    # usually gets through. A second crash is reported like any other failure.
                    if attempt == 1:
                        log(f"browser closed unexpectedly ({str(e).splitlines()[0]}) — retrying once")
                        continue
                    report_failure(site, action, page, e)
                except Exception as e:
                    report_failure(site, action, page, e)
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass


def report_failure(site: str, action: str, page, e: Exception):
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    shot = LOG_DIR / f"web-{site}-{stamp}.png"
    log(f"FAILED: {e}\n{traceback.format_exc()}")
    result_line(False, str(e).splitlines()[0] if str(e) else type(e).__name__)
    try:
        page.screenshot(path=str(shot), full_page=True)
        (LOG_DIR / f"web-{site}-{stamp}.txt").write_text(outline(page))
        send_photo(shot, f"❌ {site} · {action} failed at {page.url.split('?')[0]}\n{e}"[:1000])
    except Exception:
        send(f"❌ {site} · {action} failed: {e}"[:1000])
    sys.exit(1)


def open_profile(site: str, url: str | None):
    """
    Launch the site's Chromium profile as a plain browser — no Playwright, no
    automation flags — so you can sign in by hand (Google refuses sign-ins from
    automated browsers). Cookies stay in the profile for later recipe runs.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = p.chromium.executable_path
    profile = data_dir() / "web" / "profiles" / site
    profile.mkdir(parents=True, exist_ok=True)
    url = url or getattr(site_modules()[site], "OPEN_URL", "about:blank")
    print(f"Opening {url} with the {site} profile. Sign in, then QUIT the browser (Cmd+Q, or right-click its Dock icon → Quit) —\n"
          "closing the window leaves it running on macOS, and recipe runs can't use the profile until it quits.")
    # Same cookie-encryption settings Playwright launches with — otherwise cookies saved here are
    # encrypted with the real macOS Keychain key and recipe runs can't read them (you look logged out).
    subprocess.run([exe, f"--user-data-dir={profile}", "--use-mock-keychain", "--password-store=basic",
                    "--no-first-run", "--no-default-browser-check", url])
    state = profile.parent / f"{site}.state.json"
    data = json.loads(state.read_text()) if state.exists() else {}
    data["manual_signin"] = datetime.now().isoformat(timespec="minutes")
    state.write_text(json.dumps(data, indent=1))
    print("Browser quit — the session is saved in the profile.")


def outline(page) -> str:
    """Visible inputs/buttons/links on the page — for fixing a recipe's selectors after a failure."""
    js = """() => [...document.querySelectorAll('input,button,a,select,[role=button],[role=tab]')]
        .filter(e => e.offsetParent !== null)
        .map(e => `${e.tagName} id=${e.id} name=${e.name||''} type=${e.type||''} `
                + `aria=${e.getAttribute('aria-label')||''} href=${e.getAttribute('href')||''} | `
                + (e.innerText||e.value||e.placeholder||'').trim().slice(0,50))
        .join('\\n')"""
    return f"URL: {page.url}\nTITLE: {page.title()}\n\n{page.evaluate(js)}"


def main():
    parser = argparse.ArgumentParser(description="llm_brain browser automation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("creds")
    c.add_argument("op", choices=["set", "check"])
    c.add_argument("site")
    r = sub.add_parser("run")
    r.add_argument("site")
    r.add_argument("action")
    r.add_argument("--foreground", action="store_true", help="Run here instead of detaching")
    r.add_argument("--headless", action="store_true", help="No browser window (more likely to be blocked)")
    r.add_argument("--quiet", action="store_true", help="No ✅ Telegram message on success (failures still report)")
    o = sub.add_parser("open", help="Open a site's browser profile by hand, e.g. to sign into Google once")
    o.add_argument("site")
    o.add_argument("url", nargs="?")
    a = parser.parse_args()

    if a.cmd == "open":
        open_profile(a.site, a.url)
        return

    if a.cmd == "list":
        for name, mod in site_modules().items():
            print(f"{name}: {', '.join(mod.ACTIONS)} — {mod.__doc__.strip().splitlines()[0]}")
        return

    if a.cmd == "creds":
        svc = keychain_service(a.site)
        if a.op == "set":
            label = "username / user ID" if needs_password(a.site) else "Google account email"
            keyring.set_password(svc, "username", input(f"{a.site} {label}: ").strip())
            if needs_password(a.site):
                keyring.set_password(svc, "password", getpass.getpass(f"{a.site} password: "))
            for key in extra_secrets(a.site):
                value = getpass.getpass(f"{a.site} {key} (Enter to skip — you'll be asked on Telegram): ")
                if value:
                    keyring.set_password(svc, key, value)
            print(f"Saved to Keychain as '{svc}'.")
        else:
            ok = keyring.get_password(svc, "username") and (
                keyring.get_password(svc, "password") or not needs_password(a.site))
            print(f"{a.site}: {'credentials present' if ok else 'NO credentials'} in Keychain ('{svc}')")
        return

    mods = site_modules()
    if a.site not in mods or a.action not in mods[a.site].ACTIONS:
        sys.exit(f"Unknown site/action. Available:\n" +
                 "\n".join(f"  {n}: {', '.join(m.ACTIONS)}" for n, m in mods.items()))

    if not a.foreground:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logf = open(LOG_DIR / "web.log", "a")
        cmd = [sys.executable, __file__, "run", a.site, a.action, "--foreground"] + \
              (["--headless"] if a.headless else []) + (["--quiet"] if a.quiet else [])
        subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        print(f"Started {a.site} · {a.action} in the background. The result (or a 2FA prompt) will arrive on Telegram.")
        return

    run(a.site, a.action, headless=a.headless, quiet=a.quiet)


if __name__ == "__main__":
    main()
