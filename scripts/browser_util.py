"""
Helpers shared by site recipes (scripts/sites/).
"""

import base64
import re
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


# Fetch a URL from inside the page (the browser's own network stack, cookies and TLS fingerprint —
# so bot protection like Cloudflare treats it like the user's click) and return it base64-encoded.
PAGE_FETCH = """
async (url) => {
  const r = await fetch(url, { credentials: "include" });
  const bytes = new Uint8Array(await r.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { status: r.status, disposition: r.headers.get("content-disposition") || "", data: btoa(bin) };
}
"""


def page_fetch(page, url: str) -> tuple[int, str, bytes]:
    """GET `url` from inside the page (the user's own session and browser) → (status, filename, body).
    No click, navigation or browser download involved."""
    r = page.evaluate(PAGE_FETCH, url)
    fallback = url.split("?")[0].rsplit("/", 1)[-1]
    return r["status"], _filename({"content-disposition": r["disposition"]}, fallback), base64.b64decode(r["data"])


def _filename(headers: dict, fallback: str) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8\'\')?\"?([^\";]+)", cd)
    return m.group(1) if m else fallback


def capture_download(page, trigger: Callable[[], None], url_glob: str | None = None,
                     timeout_ms: int = 60_000) -> tuple[str, bytes]:
    """
    Run `trigger` (the clicks that start an export) and return (filename, content),
    keeping Chromium's download manager out of it (it crashed intermittently):
      - in-page Blob exports are captured in JS (BLOB_HOOK);
      - server exports matching `url_glob`: the browser's navigation to them is aborted
        and the same URL is fetched from inside the page (fetching it from Playwright's
        own HTTP client got Cloudflare "Just a moment..." challenges instead of the file);
      - anything else falls back to a regular Playwright download.
    """
    captured: dict = {}
    downloads = []

    def intercept(route):
        req = route.request
        if req.resource_type in ("document", "other") and "url" not in captured:
            captured["url"] = req.url
            route.abort()  # no navigation → no browser download
        else:
            route.continue_()

    if url_glob:
        page.route(url_glob, intercept)
    page.on("download", lambda d: downloads.append(d))
    page.evaluate(BLOB_HOOK)
    try:
        trigger()
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if "url" in captured:
                try:
                    r = page.evaluate(PAGE_FETCH, captured["url"])
                except Exception:  # context torn down by the aborted navigation — retry shortly
                    page.wait_for_timeout(300)
                    continue
                if r["status"] != 200:
                    raise RuntimeError(f"export request returned HTTP {r['status']}")
                fallback = captured["url"].split("?")[0].rsplit("/", 1)[-1]
                return _filename({"content-disposition": r["disposition"]}, fallback), base64.b64decode(r["data"])
            if downloads:
                d = downloads[0]
                return d.suggested_filename, Path(d.path()).read_bytes()
            try:
                blob = page.evaluate("window.__llmbrainCaptured")
            except Exception:  # the page's context is briefly torn down while a navigation is aborted
                blob = None
            if blob:
                return blob["name"], base64.b64decode(blob["data"].split(",", 1)[1])
            page.wait_for_timeout(250)
        raise RuntimeError(f"no file arrived within {timeout_ms // 1000}s of starting the export")
    finally:
        if url_glob:
            page.unroute(url_glob, intercept)
