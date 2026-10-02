#!/usr/bin/env python3
"""
Sync Google Calendar events into events.yaml.

Usage:
    python scripts/sync_gcal.py            # full sync
    python scripts/sync_gcal.py --dry-run  # fetch and print only, no writes
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from googleapiclient.discovery import build

from google_auth import get_credentials
from local_tz import TZ, TZ_NAME

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg


def resolve(path_str):
    return Path(path_str).expanduser()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_events(service, calendar_id: str, days_ahead: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    results = []
    page_token = None

    while True:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
            maxResults=250,
        ).execute()

        results.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def load_events_yaml(events_path: Path) -> tuple[dict, list]:
    """Return (raw_doc, events_list). Creates empty structure if file missing."""
    if events_path.exists():
        with open(events_path) as f:
            doc = yaml.safe_load(f) or {}
    else:
        doc = {}
    events = doc.get("events") or []
    return doc, events


def parse_start_date(start_str) -> datetime:
    """Parse a start string from events.yaml into a timezone-aware datetime."""
    s = str(start_str)
    try:
        if len(s) == 10:  # YYYY-MM-DD (all-day)
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
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
    Merge incoming GCal events into existing list.
    - Adds new events, updates changed ones (by external_id).
    - Removes GCal-sourced events (external_id present) whose start falls within
      the sync window but were not returned — they were deleted in Google Calendar.
    - Never touches manually created events (no external_id).
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

    # Determine which existing GCal events to drop (deleted in GCal within window)
    removed_ids = set()
    for e in existing:
        ext_id = e.get("external_id")
        if not ext_id:
            continue  # manually created — never touch
        if ext_id in fetched_ids:
            continue  # still exists in GCal
        start_dt = parse_start_date(e.get("start", ""))
        if window_start <= start_dt <= window_end:
            removed_ids.add(ext_id)
            deleted += 1

    # Build merged list, skipping removed events
    merged = [e for e in existing if e.get("external_id") not in removed_ids]

    # Rebuild index after removals
    by_external_id = {
        e["external_id"]: i
        for i, e in enumerate(merged)
        if e.get("external_id")
    }

    for ev in incoming:
        ext_id = ev["external_id"]
        if ext_id in by_external_id:
            # Update mutable fields, preserve id and created_at
            idx = by_external_id[ext_id]
            old = merged[idx]
            changed = False
            for field in ("title", "start", "end", "location", "description", "recurring", "tags", "calendar"):
                if old.get(field) != ev.get(field):
                    old[field] = ev[field]
                    changed = True
            if changed:
                updated += 1
        else:
            # Check if a manually-created event with the same title + date already exists.
            # This prevents duplicates when generate_recurring_events.py runs before this sync.
            date_prefix = str(ev["start"])[:10]
            manual_idx = next(
                (
                    i for i, e in enumerate(merged)
                    if not e.get("external_id")
                    and e.get("title") == ev["title"]
                    and str(e.get("start", "")).startswith(date_prefix)
                ),
                None,
            )
            if manual_idx is not None:
                # Adopt the manual entry: add external_id and update GCal fields
                old = merged[manual_idx]
                for field in ("title", "start", "end", "location", "description", "recurring", "tags", "calendar"):
                    old[field] = ev[field]
                old["external_id"] = ext_id
                by_external_id[ext_id] = manual_idx
                updated += 1
            else:
                # New event
                new_ev = {
                    "id": next_id,
                    "title": ev["title"],
                    "description": ev["description"],
                    "start": ev["start"],
                    "end": ev["end"],
                    "location": ev["location"],
                    "recurring": ev["recurring"],
                    "tags": ev["tags"],
                    "created_at": ev["created_at"],
                    "external_id": ext_id,
                    "calendar": ev["calendar"],
                }
                merged.append(new_ev)
                by_external_id[ext_id] = len(merged) - 1
                next_id += 1
                added += 1

    return merged, added, updated, deleted


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------



def parse_gcal_datetime(dt_field: dict) -> str:
    """Convert Google's dateTime or date field to 'YYYY-MM-DD HH:MM' (local timezone) or 'YYYY-MM-DD'."""
    if "dateTime" in dt_field:
        dt = datetime.fromisoformat(dt_field["dateTime"].replace("Z", "+00:00"))
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    elif "date" in dt_field:
        return dt_field["date"]
    return ""


def transform(gcal_event: dict, calendar_label: str) -> dict:
    title = gcal_event.get("summary", "(No title)")
    description = gcal_event.get("description", "") or ""
    location = gcal_event.get("location", "") or ""
    external_id = gcal_event["id"]
    created_at = gcal_event.get("created", "")[:10] if gcal_event.get("created") else ""

    start = parse_gcal_datetime(gcal_event.get("start", {}))
    end = parse_gcal_datetime(gcal_event.get("end", {}))

    # Map recurrence
    recurring = None
    if gcal_event.get("recurrence"):
        rule = " ".join(gcal_event["recurrence"])
        if "WEEKLY" in rule:
            recurring = "weekly"
        elif "DAILY" in rule:
            recurring = "daily"
        elif "MONTHLY" in rule:
            recurring = "monthly"

    tags = [calendar_label]

    return {
        "title": title,
        "description": description,
        "start": start,
        "end": end,
        "location": location,
        "recurring": recurring,
        "tags": tags,
        "created_at": created_at,
        "external_id": external_id,
        "calendar": calendar_label,
    }


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_event(service, calendar_id: str, event_id: str) -> None:
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def calendar_id_for_label(calendars_cfg: list, label: str) -> str:
    for cal in calendars_cfg:
        if cal["label"] == label:
            return cal["id"]
    raise ValueError(f"No calendar configured with label '{label}'. Available: {[c['label'] for c in calendars_cfg]}")


def create_event(service, calendar_id: str, title: str, start: str, end: str,
                 location: str = "", description: str = "", recurrence: str = "") -> dict:
    """
    Create an event in Google Calendar.
    start/end: 'YYYY-MM-DD HH:MM' (timed) or 'YYYY-MM-DD' (all-day).
    recurrence: RRULE string e.g. 'RRULE:FREQ=MONTHLY;BYMONTHDAY=1'
    Returns the created event resource.
    """
    def to_gcal_dt(s: str) -> dict:
        if len(s) == 10:  # all-day
            return {"date": s}
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return {"dateTime": dt.isoformat(), "timeZone": TZ_NAME}

    body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": to_gcal_dt(start),
        "end": to_gcal_dt(end),
    }

    if recurrence:
        body["recurrence"] = [recurrence]

    return service.events().insert(calendarId=calendar_id, body=body).execute()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print only, no writes")

    # --add mode
    parser.add_argument("--add", action="store_true", help="Add a new event to Google Calendar")
    parser.add_argument("--title", help="Event title")
    parser.add_argument("--start", help="Start: 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' for all-day")
    parser.add_argument("--end", help="End: 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' for all-day")
    parser.add_argument("--calendar", choices=["personal", "family"], default="personal",
                        help="Target calendar (default: personal)")
    parser.add_argument("--location", default="", help="Location (optional)")
    parser.add_argument("--description", default="", help="Description (optional)")
    parser.add_argument("--recurrence", default="", help="RRULE string e.g. 'RRULE:FREQ=MONTHLY;BYMONTHDAY=1'")

    # --delete mode
    parser.add_argument("--delete", action="store_true", help="Delete an event from Google Calendar")
    parser.add_argument("--event-id", help="GCal event ID (external_id) to delete")

    args = parser.parse_args()

    if args.add:
        missing = [f for f in ("title", "start", "end") if not getattr(args, f)]
        if missing:
            parser.error(f"--add requires: {', '.join('--' + f for f in missing)}")

    if args.delete and not args.event_id:
        parser.error("--delete requires --event-id")

    cfg = load_config()
    gcal_cfg = cfg["gcal"]

    creds_path = resolve(gcal_cfg["credentials"])
    token_path = resolve(gcal_cfg["token"])
    calendars = gcal_cfg["calendars"]
    days_ahead = gcal_cfg.get("sync_days_ahead", 60)

    window_start = datetime.now(timezone.utc)
    window_end = window_start + timedelta(days=days_ahead)

    print("Authenticating...")
    creds = get_credentials(creds_path, token_path)
    service = build("calendar", "v3", credentials=creds)
    print("Authenticated.\n")

    if args.delete:
        cal_id = calendar_id_for_label(calendars, args.calendar)
        delete_event(service, cal_id, args.event_id)
        print(f"Deleted GCal event: {args.event_id}")
        print("\nSyncing into events.yaml...")
        # fall through to sync so the deletion is reflected in events.yaml

    if args.add:
        cal_id = calendar_id_for_label(calendars, args.calendar)
        created = create_event(
            service, cal_id,
            title=args.title,
            start=args.start,
            end=args.end,
            location=args.location,
            description=args.description,
            recurrence=args.recurrence,
        )
        print(f"Created: [{args.calendar}] {args.start}  {args.title}")
        print(f"GCal ID: {created['id']}")
        print(f"Link:    {created.get('htmlLink', '')}")
        print("\nSyncing into events.yaml...")
        # fall through to sync so the new event lands in events.yaml immediately

    all_events = []

    for cal in calendars:
        cal_id = cal["id"]
        label = cal["label"]
        print(f"Fetching calendar: {label} ({cal_id})...")
        raw_events = fetch_events(service, cal_id, days_ahead)
        print(f"  Found {len(raw_events)} events")
        for e in raw_events:
            all_events.append(transform(e, label))

    print(f"\nTotal events fetched: {len(all_events)}\n")

    # Print summary
    for e in all_events:
        print(f"  [{e['calendar']:8}] {e['start']}  {e['title']}")

    if args.dry_run:
        print("\n--dry-run: no changes written.")
        return

    # Load existing events and merge
    events_path = resolve(cfg["storage"]["events"])
    _, existing = load_events_yaml(events_path)
    merged, added, updated, deleted = merge_into_yaml(existing, all_events, window_start, window_end)

    # Write back
    with open(events_path, "w") as f:
        yaml.dump({"events": merged}, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    unchanged = len(all_events) - added - updated
    print(f"\nSync complete: {added} added, {updated} updated, {unchanged} unchanged, {deleted} deleted.")


if __name__ == "__main__":
    main()
