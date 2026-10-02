#!/usr/bin/env python3
"""
Generate events from recurring event templates.

Reads recurring_events.yaml and creates individual event entries in events.yaml
for upcoming dates, up to advance_days ahead (default 60 days / ~2 months).

Idempotent: skips dates where an event with the same title already exists.

Supported recurrence types:
  daily_weekday   Every Mon–Fri; optional per-weekday time overrides via weekday_times
                  (keys: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri)

Template fields:
  id              Unique identifier (string)
  title           Event title — used as the deduplication key alongside date
  description     Event description
  tags            List of tags
  location        Location string (default "")
  recurrence:
    type          daily_weekday
    time          Default start time "HH:MM"
    duration_min  Duration in minutes
    weekday_times Map of weekday number → time override (e.g. {4: "10:00"} for Fridays)
  end_date        Optional ISO date — stop generating after this date
  skip_holidays   Skip public holidays from calendar.md (default true)
  skip_blackouts  Skip work blackout periods from calendar.md (default true)
  advance_days    How many days ahead to generate (default 60)
  skip_dates      Optional list of dates/ranges to suppress generation for:
                    - "YYYY-MM-DD"                    (exact date)
                    - {from: "YYYY-MM-DD", to: "YYYY-MM-DD"}  (inclusive range)

Usage:
    python scripts/generate_recurring_events.py
"""

import re
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
_config = yaml.safe_load((Path.home() / "Documents" / "llm_brain" / "config.yaml").read_text(encoding="utf-8"))
_storage = _config["storage"]

EVENTS_PATH = Path(_storage["events"]).expanduser()
RECURRING_EVENTS_PATH = Path(_storage["recurring_events"]).expanduser()
PROFILES_DIR = Path(_storage["profiles_dir"]).expanduser()
CALENDAR_MD = PROFILES_DIR / "calendar.md"

MONTH_ABBRS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def parse_calendar_skips() -> tuple[set, list]:
    """Parse public holidays and work blackout date ranges from calendar.md."""
    skip_dates: set[date] = set()
    skip_ranges: list[tuple[date, date]] = []

    if not CALENDAR_MD.exists():
        return skip_dates, skip_ranges

    text = CALENDAR_MD.read_text(encoding="utf-8")
    year = date.today().year

    # Public holidays: lines like "- Apr 20 — Basava Jayanti"
    holiday_re = re.compile(r"-\s+([A-Za-z]{3})\s+(\d{1,2})\s+[—-]")
    for line in text.splitlines():
        m = holiday_re.match(line.strip())
        if m:
            month = MONTH_ABBRS.get(m.group(1).lower())
            if month:
                try:
                    skip_dates.add(date(year, month, int(m.group(2))))
                except ValueError:
                    pass

    # Vacations and work blackout periods: ISO-date table rows
    # | 2026-05-01 | 2026-05-18 | ... |
    iso_range_re = re.compile(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|")
    in_section = False
    for line in text.splitlines():
        if "## Vacations" in line or "## Work Blackout" in line:
            in_section = True
            continue
        if in_section and line.startswith("##"):
            in_section = False
        if in_section:
            m = iso_range_re.search(line)
            if m:
                try:
                    skip_ranges.append((
                        date.fromisoformat(m.group(1)),
                        date.fromisoformat(m.group(2)),
                    ))
                except ValueError:
                    pass

    return skip_dates, skip_ranges


def is_skipped(d: date, skip_dates: set, skip_ranges: list) -> bool:
    return d in skip_dates or any(s <= d <= e for s, e in skip_ranges)


def is_template_skipped(d: date, skip_dates_field: list) -> bool:
    """Return True if d falls within any entry in a template's skip_dates list.

    Each entry is either:
      - A string "YYYY-MM-DD"  → exact date match
      - A dict {from: "YYYY-MM-DD", to: "YYYY-MM-DD"}  → inclusive range
    """
    for entry in (skip_dates_field or []):
        if isinstance(entry, str):
            try:
                if date.fromisoformat(entry) == d:
                    return True
            except ValueError:
                pass
        elif isinstance(entry, dict):
            try:
                from_d = date.fromisoformat(str(entry["from"]))
                to_d = date.fromisoformat(str(entry["to"]))
                if from_d <= d <= to_d:
                    return True
            except (KeyError, ValueError):
                pass
    return False


def event_exists(events: list[dict], title: str, target_date: date) -> bool:
    """Return True if an event with this title already exists on target_date."""
    prefix = target_date.isoformat()
    return any(
        str(e.get("start", "")).startswith(prefix) and e.get("title") == title
        for e in events
    )


def max_event_id(events: list[dict]) -> int:
    return max((e.get("id", 0) for e in events), default=0)


def add_minutes(time_str: str, duration_min: int) -> str:
    h, m = map(int, time_str.split(":"))
    total = h * 60 + m + duration_min
    return f"{total // 60:02d}:{total % 60:02d}"


def main() -> None:
    today = date.today()

    if not RECURRING_EVENTS_PATH.exists():
        print("No recurring_events.yaml found — skipping.")
        return

    re_data = load_yaml(RECURRING_EVENTS_PATH)
    templates = re_data.get("recurring_events", [])
    if not templates:
        print("No recurring event templates defined.")
        return

    events_data = load_yaml(EVENTS_PATH)
    events = events_data.get("events", [])

    skip_dates, skip_ranges = parse_calendar_skips()
    added = 0

    for tmpl in templates:
        title = tmpl["title"]
        description = tmpl.get("description", "")
        tags = tmpl.get("tags", [])
        location = tmpl.get("location", "")
        recurrence = tmpl.get("recurrence", {})
        advance_days = tmpl.get("advance_days", 60)
        skip_hols = tmpl.get("skip_holidays", True)
        skip_bouts = tmpl.get("skip_blackouts", True)
        end_date_raw = tmpl.get("end_date")
        end_date = date.fromisoformat(str(end_date_raw)) if end_date_raw else None
        tmpl_skip_dates = tmpl.get("skip_dates") or []

        rtype = recurrence.get("type")
        default_time = recurrence.get("time", "09:00")
        duration_min = recurrence.get("duration_min", 60)
        # weekday_times: keys may be int or str from YAML
        weekday_times_raw = recurrence.get("weekday_times", {})
        weekday_times = {int(k): v for k, v in weekday_times_raw.items()}

        generate_until = today + timedelta(days=advance_days)
        if end_date:
            generate_until = min(generate_until, end_date)

        d = today + timedelta(days=1)
        while d <= generate_until:
            # Recurrence filter
            if rtype == "daily_weekday" and d.weekday() >= 5:
                d += timedelta(days=1)
                continue

            # Skip holidays / blackouts
            if (skip_hols or skip_bouts) and is_skipped(d, skip_dates, skip_ranges):
                d += timedelta(days=1)
                continue

            # Template-level skip_dates
            if is_template_skipped(d, tmpl_skip_dates):
                d += timedelta(days=1)
                continue

            # Deduplication
            if event_exists(events, title, d):
                d += timedelta(days=1)
                continue

            # Resolve time (per-weekday override or default)
            time_str = weekday_times.get(d.weekday(), default_time)
            end_time_str = add_minutes(time_str, duration_min)

            events.append({
                "id": max_event_id(events) + 1,
                "title": title,
                "description": description,
                "start": f"{d.isoformat()} {time_str}",
                "end": f"{d.isoformat()} {end_time_str}",
                "location": location,
                "recurring": None,
                "tags": tags,
                "created_at": today.isoformat(),
            })
            added += 1
            print(f"  Added: '{title}' on {d} at {time_str}")

            d += timedelta(days=1)

    if added > 0:
        save_yaml(EVENTS_PATH, {"events": events})
        print(f"\nGenerated {added} event(s).")
    else:
        print("No new recurring events to generate.")


if __name__ == "__main__":
    main()
