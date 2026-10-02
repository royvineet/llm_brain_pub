#!/usr/bin/env python3
"""
Sync Gmail messages into emails.yaml, and send/reply/draft/label emails.

Usage:
    python scripts/sync_gmail.py                          # full sync
    python scripts/sync_gmail.py --dry-run                # fetch and print, no writes
    python scripts/sync_gmail.py --search "from:alice"    # search Gmail, print results
    python scripts/sync_gmail.py --send --to a@b.com --subject "Hi" --body "..."
    python scripts/sync_gmail.py --reply --thread-id xyz --body "..."
    python scripts/sync_gmail.py --draft --to a@b.com --subject "Hi" --body "..."
    python scripts/sync_gmail.py --archive --message-id abc
    python scripts/sync_gmail.py --mark-read --message-id abc
    python scripts/sync_gmail.py --mark-unread --message-id abc

Auth is shared with sync_gcal.py via google_auth.py (one token, both scopes).
To re-authorize: .venv/bin/python scripts/google_auth.py
"""

import argparse
import base64
import email.utils
import html.parser
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml
from googleapiclient.discovery import build

from google_auth import get_credentials
from local_tz import TZ, TZ_NAME

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def resolve(path_str: str) -> Path:
    return Path(path_str).expanduser()


# ---------------------------------------------------------------------------
# Transform helpers
# ---------------------------------------------------------------------------

def _get_header(headers: list[dict], name: str) -> str:
    """Case-insensitive header lookup."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _parse_date(date_header: str) -> str:
    """Parse RFC 2822 date string → 'YYYY-MM-DD HH:MM' in the configured timezone."""
    try:
        dt = email.utils.parsedate_to_datetime(date_header)
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def _decode_body(data: str) -> str:
    """Decode a base64url-encoded string to UTF-8 text."""
    padded = data + "=="
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


class _HTMLStripper(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(text: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(text)
    return re.sub(r"\s+", " ", stripper.get_text()).strip()


def _extract_body(payload: dict) -> str:
    """Recursively walk MIME parts; return first text/plain (or stripped HTML)."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return _decode_body(body_data).strip()

    if mime_type == "text/html" and body_data:
        return _strip_html(_decode_body(body_data))

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    return ""


def _has_attachments(payload: dict) -> bool:
    """Return True if any part is an attachment (has a filename)."""
    if payload.get("filename"):
        return True
    return any(_has_attachments(p) for p in payload.get("parts", []))


def transform(msg: dict) -> dict:
    """Convert a raw Gmail message resource to the emails.yaml schema."""
    headers = msg.get("payload", {}).get("headers", [])
    to_raw = _get_header(headers, "To")
    cc_raw = _get_header(headers, "Cc")

    to_list = [addr for _, addr in email.utils.getaddresses([to_raw]) if addr]
    cc_list = [addr for _, addr in email.utils.getaddresses([cc_raw]) if addr]

    return {
        "external_id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject": _get_header(headers, "Subject"),
        "from": _get_header(headers, "From"),
        "to": to_list,
        "cc": cc_list,
        "date": _parse_date(_get_header(headers, "Date")),
        "labels": msg.get("labelIds", []),
        "snippet": msg.get("snippet", ""),
        "body": _extract_body(msg.get("payload", {})),
        "has_attachments": _has_attachments(msg.get("payload", {})),
        "created_at": datetime.now(TZ).strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _build_query(labels: list[str], days_back: int) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y/%m/%d")
    label_clause = " OR ".join(f"label:{lb.lower()}" for lb in labels)
    # Only the Primary tab. Negative "-category:..." clauses return nothing via the API.
    primary = "category:primary"
    return f"after:{cutoff} ({label_clause}) {primary}"


def fetch_messages(service, labels: list[str], days_back: int) -> list[dict]:
    """Fetch messages matching the label/date query and return full resources."""
    query = _build_query(labels, days_back)
    print(f"  Query: {query}")

    # Page through message list
    message_ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me",
            q=query,
            pageToken=page_token,
            maxResults=500,
        ).execute()
        for item in resp.get("messages", []):
            message_ids.append(item["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"  Found {len(message_ids)} messages — fetching full content...")

    # Fetch full message resources
    messages: list[dict] = []
    for msg_id in message_ids:
        msg = service.users().messages().get(
            userId="me",
            id=msg_id,
            format="full",
        ).execute()
        messages.append(msg)

    return messages


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------

def load_emails_yaml(emails_path: Path) -> tuple[dict, list]:
    """Return (raw_doc, emails_list). Creates empty structure if file missing."""
    if emails_path.exists():
        with open(emails_path) as f:
            doc = yaml.safe_load(f) or {}
    else:
        doc = {}
    return doc, doc.get("emails") or []


def _parse_email_date(date_str) -> datetime:
    s = str(date_str)
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ).astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def merge_into_yaml(
    existing: list,
    incoming: list,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list, int, int, int]:
    """
    Merge incoming Gmail messages into the existing list.
    - Adds new messages, updates changed ones (by external_id).
    - Removes GCal-sourced messages (external_id present) whose date falls within
      the sync window but were not returned — they were deleted in Gmail.
    - Never touches manually created entries (no external_id).
    Returns (merged_list, added, updated, deleted).
    """
    fetched_ids = {ev["external_id"] for ev in incoming}

    by_external_id = {
        e["external_id"]: i
        for i, e in enumerate(existing)
        if e.get("external_id")
    }
    next_id = max((e.get("id", 0) for e in existing), default=0) + 1
    added = updated = deleted = 0

    # Identify synced messages deleted from Gmail within the window
    removed_ids: set[str] = set()
    for e in existing:
        ext_id = e.get("external_id")
        if not ext_id:
            continue
        if ext_id in fetched_ids:
            continue
        date_dt = _parse_email_date(e.get("date", ""))
        if window_start <= date_dt <= window_end:
            removed_ids.add(ext_id)
            deleted += 1

    merged = [e for e in existing if e.get("external_id") not in removed_ids]

    # Rebuild index after removals
    by_external_id = {
        e["external_id"]: i
        for i, e in enumerate(merged)
        if e.get("external_id")
    }

    mutable_fields = ("subject", "from", "to", "cc", "labels", "snippet", "body", "has_attachments")

    for ev in incoming:
        ext_id = ev["external_id"]
        if ext_id in by_external_id:
            idx = by_external_id[ext_id]
            old = merged[idx]
            changed = False
            for field in mutable_fields:
                if old.get(field) != ev.get(field):
                    old[field] = ev[field]
                    changed = True
            if changed:
                updated += 1
        else:
            new_entry = {"id": next_id, **{k: ev[k] for k in (
                "external_id", "thread_id", "subject", "from", "to", "cc",
                "date", "labels", "snippet", "body", "has_attachments", "created_at",
            )}}
            merged.append(new_entry)
            by_external_id[ext_id] = len(merged) - 1
            next_id += 1
            added += 1

    return merged, added, updated, deleted


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_messages(service, query: str) -> None:
    """Search Gmail with the given query and print results."""
    print(f"Searching: {query}\n")
    resp = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=50,
    ).execute()
    items = resp.get("messages", [])
    if not items:
        print("No results.")
        return

    print(f"{len(items)} result(s):\n")
    for item in items:
        msg = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        headers = msg.get("payload", {}).get("headers", [])
        subject = _get_header(headers, "Subject") or "(no subject)"
        sender = _get_header(headers, "From")
        date = _parse_date(_get_header(headers, "Date"))
        labels = msg.get("labelIds", [])
        unread = "UNREAD" in labels
        print(f"  {'[UNREAD] ' if unread else ''}{date}  {sender}")
        print(f"    Subject: {subject}")
        print(f"    message_id: {item['id']}  thread_id: {msg.get('threadId', '')}")
        print()


# ---------------------------------------------------------------------------
# Send / Draft / Labels
# ---------------------------------------------------------------------------

def _build_mime(to: str, subject: str, body: str, cc: str = "", attachments: list[str] | None = None):
    if not attachments:
        msg = MIMEText(body)
    else:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body))
        for path_str in attachments:
            path = Path(path_str).expanduser()
            ctype, _ = mimetypes.guess_type(str(path))
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            part = MIMEBase(maintype, subtype)
            part.set_payload(path.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    return msg


def send_email(
    service,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    thread_id: str = "",
    attachments: list[str] | None = None,
) -> dict:
    """Send a new email or reply to an existing thread."""
    if thread_id and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    mime = _build_mime(to, subject, body, cc, attachments)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    message_body: dict = {"raw": raw}
    if thread_id:
        message_body["threadId"] = thread_id
    result = service.users().messages().send(userId="me", body=message_body).execute()
    print(f"Sent. message_id: {result['id']}")
    return result


def create_draft(
    service,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    thread_id: str = "",
    attachments: list[str] | None = None,
) -> dict:
    """Create a Gmail draft."""
    mime = _build_mime(to, subject, body, cc, attachments)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    message_body: dict = {"raw": raw}
    if thread_id:
        message_body["threadId"] = thread_id
    result = service.users().drafts().create(
        userId="me",
        body={"message": message_body},
    ).execute()
    print(f"Draft created. draft_id: {result['id']}")
    return result


def archive_message(service, message_id: str) -> None:
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["INBOX"]},
    ).execute()
    print(f"Archived: {message_id}")


def mark_read(service, message_id: str) -> None:
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()
    print(f"Marked read: {message_id}")


def trash_message(service, message_id: str) -> None:
    service.users().messages().trash(userId="me", id=message_id).execute()
    print(f"Trashed: {message_id}")


def mark_unread(service, message_id: str) -> None:
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": ["UNREAD"]},
    ).execute()
    print(f"Marked unread: {message_id}")


def download_attachments(service, message_id: str, attachments_dir: Path) -> None:
    """Download all attachments from a message into attachments_dir/<message_id>/."""
    msg = service.users().messages().get(
        userId="me",
        id=message_id,
        format="full",
    ).execute()

    dest_dir = attachments_dir / message_id
    parts = _collect_attachment_parts(msg.get("payload", {}))

    if not parts:
        print(f"No attachments found in message {message_id}.")
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {len(parts)} attachment(s) to {dest_dir}/")

    for part in parts:
        filename = part.get("filename") or "attachment"
        attachment_id = part.get("body", {}).get("attachmentId")
        inline_data = part.get("body", {}).get("data")

        if attachment_id:
            resp = service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
            data = base64.urlsafe_b64decode(resp["data"] + "==")
        elif inline_data:
            data = base64.urlsafe_b64decode(inline_data + "==")
        else:
            print(f"  Skipping '{filename}' — no data.")
            continue

        out_path = dest_dir / filename
        out_path.write_bytes(data)
        print(f"  Saved: {filename} ({len(data):,} bytes)")


def _collect_attachment_parts(payload: dict) -> list[dict]:
    """Recursively collect MIME parts that are attachments (have a filename)."""
    parts = []
    if payload.get("filename") and payload.get("body", {}).get("attachmentId"):
        parts.append(payload)
    for part in payload.get("parts", []):
        parts.extend(_collect_attachment_parts(part))
    return parts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Gmail sync and actions for llm_brain")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print only, no writes")

    # Search
    parser.add_argument("--search", metavar="QUERY", help="Search Gmail with Gmail query syntax")

    # Send / reply
    parser.add_argument("--send", action="store_true", help="Send a new email")
    parser.add_argument("--reply", action="store_true", help="Reply to an existing thread")
    parser.add_argument("--to", help="Recipient email address")
    parser.add_argument("--subject", default="", help="Email subject")
    parser.add_argument("--body", default="", help="Email body (plain text)")
    parser.add_argument("--cc", default="", help="CC address(es), comma-separated")
    parser.add_argument("--attach", action="append", metavar="PATH",
                        help="Path to a file to attach; repeat for multiple attachments")

    # Draft
    parser.add_argument("--draft", action="store_true", help="Create a draft instead of sending")

    # Label operations
    parser.add_argument("--archive", action="store_true", help="Archive a message (remove from INBOX)")
    parser.add_argument("--mark-read", action="store_true", help="Mark a message as read")
    parser.add_argument("--mark-unread", action="store_true", help="Mark a message as unread")
    parser.add_argument("--trash", action="store_true", help="Move a message to trash")
    parser.add_argument("--message-id", help="Gmail message ID (external_id) for label operations")

    # Attachment download
    parser.add_argument("--download-attachments", action="store_true",
                        help="Download all attachments for --message-id to attachments/<message_id>/")

    # Thread ID for replies
    parser.add_argument("--thread-id", help="Gmail thread ID for --reply")

    args = parser.parse_args()

    # Validate
    if args.send or args.draft:
        if not args.to:
            parser.error("--send/--draft requires --to")
    if args.reply:
        if not args.thread_id:
            parser.error("--reply requires --thread-id")
        if not args.body:
            parser.error("--reply requires --body")
    if (args.archive or args.mark_read or args.mark_unread or args.trash or args.download_attachments) and not args.message_id:
        parser.error("--archive/--mark-read/--mark-unread/--trash/--download-attachments require --message-id")
    if args.attach:
        if not (args.send or args.reply or args.draft):
            parser.error("--attach requires --send, --reply, or --draft")
        missing = [p for p in args.attach if not Path(p).expanduser().is_file()]
        if missing:
            parser.error(f"attachment not found: {', '.join(missing)}")

    cfg = load_config()
    gmail_cfg = cfg["gmail"]
    emails_path = resolve(cfg["storage"]["emails"])
    attachments_dir = emails_path.parent / "attachments"

    creds_path = resolve(gmail_cfg["credentials"])
    token_path = resolve(gmail_cfg["token"])

    print("Authenticating...")
    creds = get_credentials(creds_path, token_path)
    service = build("gmail", "v1", credentials=creds)
    print("Authenticated.\n")

    # --- Action modes ---

    if args.search:
        search_messages(service, args.search)
        return

    if args.send or (args.reply and not args.draft):
        send_email(
            service,
            to=args.to or "",
            subject=args.subject,
            body=args.body,
            cc=args.cc,
            thread_id=args.thread_id or "",
            attachments=args.attach,
        )
        return

    if args.draft:
        create_draft(
            service,
            to=args.to or "",
            subject=args.subject,
            body=args.body,
            cc=args.cc,
            thread_id=args.thread_id or "",
            attachments=args.attach,
        )
        return

    if args.archive:
        archive_message(service, args.message_id)
        return

    if args.mark_read:
        mark_read(service, args.message_id)
        return

    if args.mark_unread:
        mark_unread(service, args.message_id)
        return

    if args.trash:
        trash_message(service, args.message_id)
        return

    if args.download_attachments:
        download_attachments(service, args.message_id, attachments_dir)
        return

    # --- Sync mode (default) ---

    days_back = gmail_cfg.get("sync_days_back", 7)
    labels = gmail_cfg.get("labels_to_sync", ["INBOX", "SENT"])

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=days_back)

    print(f"Fetching Gmail ({days_back} days back, labels: {labels})...")
    raw_messages = fetch_messages(service, labels, days_back)
    incoming = [transform(m) for m in raw_messages]

    print(f"\nTotal messages fetched: {len(incoming)}")
    for ev in incoming:
        unread = "UNREAD" in ev.get("labels", [])
        print(f"  {'[U]' if unread else '   '} {ev['date']}  {ev['from'][:40]}  {ev['subject'][:50]}")

    if args.dry_run:
        print("\n--dry-run: no changes written.")
        return

    _, existing = load_emails_yaml(emails_path)
    merged, added, updated, deleted = merge_into_yaml(existing, incoming, window_start, window_end)

    with open(emails_path, "w") as f:
        yaml.dump({"emails": merged}, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    unchanged = len(incoming) - added - updated
    print(f"\nSync complete: {added} added, {updated} updated, {unchanged} unchanged, {deleted} deleted.")


if __name__ == "__main__":
    main()
