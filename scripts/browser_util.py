"""
Helpers shared by site recipes (scripts/sites/).
"""

import base64
import time
from pathlib import Path
from typing import Callable

# Many web apps build exports in the page (SheetJS & co.): a Blob, an object URL,
# and a synthetic click on an <a download>. Chromium's own download handling
# for these crashed intermittently on macOS (SIGSEGV in the browser
# process, headful and headless alike), so we grab the Blob in-page instead and
# swallow the click — no browser download happens at all.
BLOB_HOOK = """
(() => {
  if (window.__llmbrainBlobHook) return;
  window.__llmbrainBlobHook = true;
  window.__llmbrainCaptured = null;
  const blobs = new Map();
  const create = URL.createObjectURL;
  URL.createObjectURL = function (obj) {
    const url = create.call(URL, obj);
    if (obj instanceof Blob) blobs.set(url, obj);
    return url;
  };
  const grab = (a) => {
    const blob = blobs.get(a.href);
    if (!blob) return false;
    const fr = new FileReader();
    fr.onload = () => { window.__llmbrainCaptured = { name: a.download || "download", data: fr.result }; };
    fr.readAsDataURL(blob);
    return true;
  };
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () { if (grab(this)) return; return click.call(this); };
  const dispatch = EventTarget.prototype.dispatchEvent;
  EventTarget.prototype.dispatchEvent = function (ev) {
    if (this instanceof HTMLAnchorElement && ev.type === "click" && grab(this)) return true;
    return dispatch.call(this, ev);
  };
})();
"""


def capture_download(page, trigger: Callable[[], None], timeout_ms: int = 60_000) -> tuple[str, bytes]:
    """
    Run `trigger` (the clicks that start an export) and return (filename, content).
    In-page Blob exports are captured in JS; anything that turns into a real
    browser download (server-generated files) is taken from Playwright instead.
    """
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    page.evaluate(BLOB_HOOK)
    trigger()
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if downloads:
            d = downloads[0]
            return d.suggested_filename, Path(d.path()).read_bytes()
        captured = page.evaluate("window.__llmbrainCaptured")
        if captured:
            return captured["name"], base64.b64decode(captured["data"].split(",", 1)[1])
        page.wait_for_timeout(250)
    raise RuntimeError(f"no file arrived within {timeout_ms // 1000}s of starting the export")
