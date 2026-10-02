#!/usr/bin/env python3
"""
Compact snapshot of "what matters right now", prepended to every Telegram
message so Claude can answer most questions without reading files (each file
read is a full model round-trip — the main source of latency).

Contains: current time and day type, upcoming holidays/trips, open tasks
(compact, one line each), the next 7 days of non-routine events, recurring
templates with their next occurrence, and the latest Primary-inbox emails.

Usage:
    python scripts/brain_context.py        # print the snapshot
"""

from datetime import datetime, timedelta
from pathlib import Path

import notify as n
from local_tz import TZ, TZ_NAME
from generate_recurring_tasks import next_occurrence

EVENT_DAYS = 7
TASK_HORIZON_DAYS = 60   # directives: hide tasks due more than 2 months out
EMAILS = 6


def snapshot(now: datetime | None = None) -> str:
    app_cfg = n.load_yaml(n.CONFIG_PATH)
    storage = app_cfg["storage"]
    path = lambda key: Path(storage[key]).expanduser()
    profiles = path("profiles_dir")

    now = now or datetime.now(TZ)
    today = now.date()
    holidays, periods = n.parse_calendar_md(profiles / "calendar.md", today.year)
    dtype, reason = n.day_type(today, holidays, periods)

    out = [f"NOW: {now:%a %Y-%m-%d %H:%M} {TZ_NAME} · {dtype}{' (' + reason + ')' if reason else ''}"]

    upcoming = [f"{d:%a %d %b} {name}" for d, name in sorted(holidays.items()) if today < d <= today + timedelta(days=21)]
    upcoming += [f"{d1:%d %b}–{d2:%d %b} {label} ({section})" for d1, d2, label, section in periods
                 if d2 >= today and d1 <= today + timedelta(days=30)]
    if upcoming:
        out.append("UPCOMING: " + "; ".join(upcoming))

    # Open tasks
    tasks = n.load_yaml(path("tasks")).get("tasks", [])
    open_ = sorted(n.open_tasks(tasks), key=lambda t: (str(t.get("due_date") or "9999"), n.task_sort_key(t)))
    hidden = 0
    lines = []
    for t in open_:
        due = n.parse_day(t.get("due_date"))
        if due and (due - today).days > TASK_HORIZON_DAYS:
            hidden += 1
            continue
        if t.get("lapse") and due and due < today:
            continue  # lapsed med
        meta = [t.get("priority", ""), n.tier(t)]
        if t.get("actionable"):
            meta.append(t["actionable"])
        if t.get("remind_at"):
            meta.append(f"@{t['remind_at']}")
        when = n.due_phrase(t, today) or "no date"
        lines.append(f"#{t['id']} {t['title']} — {when} [{', '.join(meta)}] ({t.get('category', '')})")
    out.append(f"OPEN TASKS ({len(lines)}{f', +{hidden} due >2 months out, not shown' if hidden else ''}):")
    out += [f"  {l}" for l in lines] or ["  (none)"]

    done_today = [t for t in tasks if str(t.get("completed_at")) == today.isoformat()]
    if done_today:
        shown = "; ".join(f"#{t['id']} {t['title']} ({t['status']})" for t in done_today[-8:])
        out.append(f"CLOSED TODAY ({len(done_today)}): {shown}")

    # Events
    routine = {r.get("title") for r in n.load_yaml(path("recurring_events")).get("recurring_events", [])}
    events = sorted(
        (e for e in n.load_yaml(path("events")).get("events", [])
         if (d := n.parse_day(e.get("start"))) and today <= d <= today + timedelta(days=EVENT_DAYS)
         and e.get("title") not in routine),
        key=lambda e: str(e.get("start")),
    )
    out.append(f"EVENTS next {EVENT_DAYS} days:")
    for e in events:
        dt = n.parse_dt(e.get("start"))
        when = f"{dt:%a %d %b %H:%M}" if dt else f"{n.parse_day(e['start']):%a %d %b} all-day"
        cal = f" [{e['calendar']}]" if e.get("calendar") else ""
        where = " ".join(str(e.get("location") or "").split())[:60]
        out.append(f"  {when} {e['title']}{cal}" + (f" @ {where}" if where else ""))
    if not events:
        out.append("  (none)")

    # Recurring templates → next occurrence
    rts = n.load_yaml(path("recurring_tasks")).get("recurring_tasks", [])
    rec = []
    for r in rts:
        nxt = next_occurrence(r.get("recurrence", {}), today)
        extra = f" @{r['remind_at']}" if r.get("remind_at") else ""
        rec.append(f"{r['title']} next {nxt:%a %d %b}{extra}" if nxt else r["title"])
    if rec:
        out.append("RECURRING: " + "; ".join(rec))

    # Latest emails
    emails = n.load_yaml(path("emails")).get("emails", []) if storage.get("emails") else []
    emails = sorted(emails, key=lambda m: str(m.get("date", "")), reverse=True)[:EMAILS]
    if emails:
        out.append("LATEST INBOX (Primary):")
        for m in emails:
            unread = "•" if "UNREAD" in (m.get("labels") or []) else " "
            sender = str(m.get("from", "")).split("<")[0].strip()[:30]
            out.append(f"  {unread} {str(m.get('date', ''))[:16]} {sender}: {str(m.get('subject', ''))[:70]}")

    return "\n".join(out)


if __name__ == "__main__":
    print(snapshot())
