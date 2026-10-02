#!/usr/bin/env python3
"""
Generate tasks from recurring task templates.

Reads recurring_tasks.yaml and creates one-off task entries in tasks.yaml
when the next occurrence of a recurring task is due (within advance_days).

Missed occurrences are generated if they are within 7 days past due.
Occurrences older than 7 days are skipped silently.

Recurrence types supported:
  monthly_nth_weekday:
    n:       nth occurrence (1 = first, 2 = second, ...)
    weekday: 0=Monday ... 6=Sunday
  monthly_fixed_day:
    day:     fixed day of the month (1–28)
  every_n_days:
    every:   interval in days (2 = every alternate day)
    start:   "YYYY-MM-DD" — first occurrence; later ones are start + k*every

skip_dates (optional):
  A list of dates or date ranges to suppress generation for.
  Individual date:  "YYYY-MM-DD"
  Range:            {from: "YYYY-MM-DD", to: "YYYY-MM-DD"}
  Occurrences whose due_date falls in a skip window are silently passed over
  and the next valid occurrence is used instead.

Usage:
    python scripts/generate_recurring_tasks.py
"""

import calendar
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).parent.parent
_config = yaml.safe_load((Path.home() / "Documents" / "llm_brain" / "config.yaml").read_text(encoding="utf-8"))
_storage = _config["storage"]

TASKS_PATH = Path(_storage["tasks"]).expanduser()
EVENTS_PATH = Path(_storage["events"]).expanduser()
RECURRING_PATH = Path(_storage["recurring_tasks"]).expanduser()

# How many days either side of the computed due_date to look for an existing
# task or event with the same title before generating a new one.  Covers the
# case where the user intentionally rescheduled the task/event to a nearby date.
# Capped at half the recurrence interval (see reschedule_window) so a frequent
# recurrence — weekly, every 2 days — isn't blocked by its previous occurrence.
RESCHEDULE_WINDOW_DAYS = 30


def reschedule_window(recurrence: dict) -> int:
    interval = {"weekly": 7}.get(recurrence.get("type"), 30)
    if recurrence.get("type") == "every_n_days":
        interval = int(recurrence["every"])
    return min(RESCHEDULE_WINDOW_DAYS, interval // 2)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def nth_weekday_of_month(year: int, month: int, n: int, weekday: int) -> Optional[date]:
    """Return the date of the nth occurrence of weekday in the given month.
    Returns None if the month has fewer than n occurrences of that weekday."""
    count = 0
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
    return None


def next_occurrence(recurrence: dict, on_or_after: date) -> Optional[date]:
    """Return the next occurrence date on or after `on_or_after`."""
    rtype = recurrence.get("type")

    if rtype == "monthly_nth_weekday":
        n = recurrence["n"]
        weekday = recurrence["weekday"]
        year, month = on_or_after.year, on_or_after.month
        for _ in range(14):  # look ahead at most 14 months
            occ = nth_weekday_of_month(year, month, n, weekday)
            if occ and occ >= on_or_after:
                return occ
            month += 1
            if month > 12:
                month = 1
                year += 1

    if rtype == "monthly_fixed_day":
        day = recurrence["day"]
        year, month = on_or_after.year, on_or_after.month
        for _ in range(14):  # look ahead at most 14 months
            try:
                occ = date(year, month, day)
                if occ >= on_or_after:
                    return occ
            except ValueError:
                pass  # day out of range for this month — skip
            month += 1
            if month > 12:
                month = 1
                year += 1

    if rtype == "every_n_days":
        every = int(recurrence["every"])
        start = date.fromisoformat(str(recurrence["start"]))
        if on_or_after <= start:
            return start
        steps = -(-(on_or_after - start).days // every)  # ceil
        return start + timedelta(days=steps * every)

    if rtype == "weekly":
        weekday = recurrence["weekday"]
        days_ahead = (weekday - on_or_after.weekday()) % 7
        return on_or_after + timedelta(days=days_ahead)

    return None


_UNDATED = object()  # sentinel for "matched but no date"


def find_existing_task(tasks: list[dict], title: str, due_date: date, window: int):
    """Return the due_date of an existing task with the same title within the
    reschedule window, the _UNDATED sentinel if a match exists but has no date,
    or None if no match is found.  Catches the case where the user manually
    moved the task to a nearby date."""
    for t in tasks:
        if t.get("title") != title:
            continue
        raw = t.get("due_date")
        if raw is None:
            return _UNDATED
        try:
            existing = date.fromisoformat(str(raw))
        except ValueError:
            continue
        if abs((existing - due_date).days) <= window:
            return existing
    return None


def find_existing_event(events: list[dict], title: str, due_date: date, window: int) -> Optional[date]:
    """Return the start date of an existing event with the same title within the
    reschedule window, or None if no match is found."""
    for e in events:
        if e.get("title") != title:
            continue
        raw = e.get("start")
        if not raw:
            continue
        try:
            # start may be "YYYY-MM-DD HH:MM" or "YYYY-MM-DD"
            existing = date.fromisoformat(str(raw).split()[0])
        except ValueError:
            continue
        if abs((existing - due_date).days) <= window:
            return existing
    return None


def max_task_id(tasks: list[dict]) -> int:
    return max((t.get("id", 0) for t in tasks), default=0)


def is_date_skipped(d: date, skip_dates: list) -> bool:
    """Return True if d falls within any entry in skip_dates.

    Each entry is either:
      - A string "YYYY-MM-DD"  → exact date match
      - A dict {from: "YYYY-MM-DD", to: "YYYY-MM-DD"}  → inclusive range
    """
    for entry in (skip_dates or []):
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


def find_actionable_occurrence(
    recurrence: dict,
    last_generated: Optional[date],
    today: date,
    skip_dates: Optional[list] = None,
) -> Optional[date]:
    """Find the next occurrence that is either upcoming (within advance window)
    or missed but not stale (within 7 days past due).
    Stale occurrences and dates in skip_dates are silently passed over."""
    STALE_DAYS = 7
    # When last_generated is None (never run), look back by the grace window so a
    # first occurrence that landed a few days before today is still picked up,
    # rather than skipping straight to next month.
    search_from = (
        (last_generated + timedelta(days=1))
        if last_generated
        else today - timedelta(days=STALE_DAYS)
    )

    for _ in range(24):  # safety cap — at most 2 years of searching
        due = next_occurrence(recurrence, search_from)
        if due is None:
            return None
        if today > due + timedelta(days=STALE_DAYS):
            # Stale — skip to next occurrence
            search_from = due + timedelta(days=1)
            continue
        if is_date_skipped(due, skip_dates):
            print(f"    (occurrence {due} is in skip_dates — skipping)")
            search_from = due + timedelta(days=1)
            continue
        return due

    return None


def main() -> None:
    today = date.today()

    if not RECURRING_PATH.exists():
        print("No recurring_tasks.yaml found — skipping.")
        return

    recurring_data = load_yaml(RECURRING_PATH)
    recurring_tasks = recurring_data.get("recurring_tasks", [])

    if not recurring_tasks:
        print("No recurring tasks defined.")
        return

    tasks_data = load_yaml(TASKS_PATH)
    tasks = tasks_data.get("tasks", [])

    events_data = load_yaml(EVENTS_PATH)
    events = events_data.get("events", [])

    generated = 0
    changed_recurring = False

    for rt in recurring_tasks:
        title = rt["title"]
        recurrence = rt.get("recurrence", {})
        advance_days = rt.get("advance_days", 0)
        last_generated_raw = rt.get("last_generated")
        last_generated = (
            date.fromisoformat(str(last_generated_raw)) if last_generated_raw else None
        )

        skip_dates = rt.get("skip_dates") or []
        due_date = find_actionable_occurrence(recurrence, last_generated, today, skip_dates)

        if due_date is None:
            print(f"  '{title}': could not compute next occurrence — check recurrence config")
            continue

        generate_from = due_date - timedelta(days=advance_days)

        if today < generate_from:
            print(f"  '{title}': next due {due_date} — not yet")
            continue

        window = reschedule_window(recurrence)
        existing_task_date = find_existing_task(tasks, title, due_date, window)
        if existing_task_date is not None:
            if existing_task_date is _UNDATED:
                print(f"  '{title}': undated task already exists — skipping")
            elif existing_task_date == due_date:
                print(f"  '{title}': task for {due_date} already exists — skipping")
            else:
                print(
                    f"  '{title}': task already exists on {existing_task_date} "
                    f"(within {window}d of {due_date}) — "
                    f"skipping (may have been rescheduled)"
                )
            continue

        existing_event_date = find_existing_event(events, title, due_date, window)
        if existing_event_date is not None:
            print(
                f"  '{title}': event already exists on {existing_event_date} "
                f"(within {window}d of {due_date}) — "
                f"skipping (may have been moved to calendar)"
            )
            continue

        new_task = {
            "id": max_task_id(tasks) + 1,
            "title": title,
            "description": rt.get("description", ""),
            "status": "pending",
            "priority": rt.get("priority", "medium"),
            "category": rt.get("category", "personal"),
            "due_date": due_date.isoformat(),
            "tags": rt.get("tags", []),
            "created_at": today.isoformat(),
            "ttl_days": rt.get("ttl_days"),
            "completed_at": None,
        }
        # Reminder settings (see notify.py) pass through from the template.
        for field in ("remind", "remind_at", "remind_days_before", "actionable", "lapse"):
            if rt.get(field) is not None:
                new_task[field] = rt[field]
        tasks.append(new_task)
        rt["last_generated"] = due_date.isoformat()
        changed_recurring = True
        generated += 1
        print(f"  Generated: '{title}' due {due_date} (id {new_task['id']})")

    if generated > 0:
        save_yaml(TASKS_PATH, {"tasks": tasks})

    if changed_recurring:
        save_yaml(RECURRING_PATH, {"recurring_tasks": recurring_tasks})

    if generated == 0:
        print("No recurring tasks to generate.")
    else:
        print(f"Generated {generated} task(s).")


if __name__ == "__main__":
    main()
