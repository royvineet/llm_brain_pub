#!/usr/bin/env python3
"""
Smart Telegram notifications for llm_brain. Run every 15 min by launchd
(see scripts/install_launchd.sh); each run decides what, if anything, is worth
sending right now. Deterministic — no LLM calls.

Model
-----
Every open task gets a reminder TIER:
  critical   On its due day: pinged at `remind_at` (else the first slot), re-pinged
             once after `critical_repeat_min` if still open. Allowed during work
             hours. Heads-up and overdue days are batched like `important`.
             For meds, payments due today.
  important  One batched, actionable ping (✓ / Snooze buttons) in the best slot
             for the task's `actionable` kind, on its due day, on each of its
             `remind_days_before` days, and on overdue days 1, 3 and 7.
  routine    Digest only.
Explicit `remind:` on the task wins; otherwise high → important, medium with a
due date → important, everything else → routine.

`lapse: true` — a missed occurrence just expires (meds, "take X"): no overdue
pings and it drops out of the digest the day after it was due.

Every task has an ACTIONABLE kind (default `anytime`) — when it can be done:
  business_hours  calls/visits that need shops/banks/clinics open
  anytime         online or at home
  weekend         needs a free day (tinkering, outings)
  office          needs to be at the office (printouts)

Each day is a DAY TYPE with its own slots (times configurable under notify:):
  workday  digest 09:00 (planning slot) · lunch 13:00 · evening 19:30
  offday   digest 08:30 · errands 10:30 · evening 19:00      (weekends, public holidays)
  away     digest 09:00 only + critical + event reminders  (vacations, blackouts)

Which kinds each slot delivers:
  workday: digest → office · lunch → business_hours · evening → anytime
           (weekend-kind tasks are held for the next off-day)
  offday:  errands → business_hours, office, weekend · evening → anytime
Nothing non-critical lands during work hours except the lunch slot.

Fatigue controls: quiet hours (critical pings may continue until
`critical_until`, so a 22:00 med's one repeat still lands), each item at most once per day, at most
`daily_cap` slot messages per day (critical, digest, and event reminders don't
count), items batched into one message per slot, overdue items fade to the
digest after day 7 and to the Sunday review after `stale_after_days`.

On off-days the digest also suggests up to `backlog_suggestions` undated tasks
(rotating, least-recently-suggested first) and one active high-priority goal.

Usage:
    python scripts/notify.py                       # normal run (what launchd calls)
    python scripts/notify.py --dry-run             # print what would be sent now
    python scripts/notify.py --dry-run --at "2026-10-03 10:30"   # simulate a time
    python scripts/notify.py --force digest|review|SLOT          # send one now, ignoring schedule/state
"""

import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from local_tz import TZ
from telegram_notify import send

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"

DEFAULTS = {
    "quiet_start": "22:30",
    "quiet_end": "07:00",
    "critical_until": "23:30",       # critical pings may run past quiet_start until this
    "workday_slots": {"digest": "09:00", "lunch": "13:00", "evening": "19:30"},
    "offday_slots": {"digest": "08:30", "errands": "10:30", "evening": "19:00"},
    "away_slots": {"digest": "09:00"},
    "review_time": "11:00",          # Sundays
    "daily_cap": 3,
    "critical_repeat_min": 60,
    "event_lead_min": 30,
    "stale_after_days": 14,
    "lookahead_days": 3,
    "trip_warning_days": 7,
    "backlog_suggestions": 3,
}

SLOT_KINDS = {
    "workday": {"digest": {"office"}, "lunch": {"business_hours"}, "evening": {"anytime"}},
    "offday": {"errands": {"business_hours", "office", "weekend"}, "evening": {"anytime"}},
    "away": {},
}
SLOT_HEADERS = {
    "digest": "📋 Before you head out",
    "lunch": "🕐 Lunch break — places are open now",
    "errands": "🧾 Errand window",
    "evening": "🌙 This evening",
}
OVERDUE_PING_DAYS = {1, 3, 7}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
DEPART_RE = re.compile(r"[Dd]epart(?:\s+\w+)?\s+by\s+(\d{1,2}:\d{2})")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def hm(s: str) -> tuple[int, int]:
    h, m = str(s).split(":")
    return int(h), int(m)


def parse_dt(s) -> datetime | None:
    """Parse 'YYYY-MM-DD HH:MM' (timed) → aware datetime; all-day → None."""
    try:
        return datetime.strptime(str(s or "")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError:
        return None


def parse_day(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def parse_calendar_md(path: Path, year: int) -> tuple[dict[date, str], list[tuple[date, date, str, str]]]:
    """
    Return (holidays, periods) from profiles/calendar.md.
    holidays: {date: name} from "- Mon D — Name" bullets under a "Public Holidays YYYY" heading.
    periods:  (from, to, label, section) from any table row starting with two ISO dates.
    """
    holidays: dict[date, str] = {}
    periods: list[tuple[date, date, str, str]] = []
    if not path.exists():
        return holidays, periods

    section, section_year = "", year
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            m = re.search(r"(\d{4})", section)
            section_year = int(m.group(1)) if m else year
            continue
        if section.lower().startswith("public holidays"):
            m = re.match(r"-\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+[—-]+\s+(.+)", line)
            if m:
                try:
                    d = datetime.strptime(f"{m.group(1)} {m.group(2)} {section_year}", "%b %d %Y").date()
                    holidays[d] = m.group(3).strip()
                except ValueError:
                    pass
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3:
            d1, d2 = parse_day(cells[0]), parse_day(cells[1])
            if d1 and d2:
                periods.append((d1, d2, cells[2], section))
    return holidays, periods


def parse_goals_md(path: Path) -> list[dict]:
    """Active goals as [{name, priority, target}] from profiles/goals.md."""
    goals, cur = [], None
    if not path.exists():
        return goals
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = {"name": line[3:].strip(), "priority": "", "status": "", "target": ""}
            goals.append(cur)
        elif cur and (m := re.match(r"-\s*(Priority|Status|Target):\s*(.+)", line.strip())):
            cur[m.group(1).lower()] = m.group(2).strip()
    return [g for g in goals if g["status"].lower().startswith("active")]


class State:
    """Remembers what was sent: keys → timestamp, plus per-day counters."""

    def __init__(self, path: Path):
        self.path = path
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.sent: dict[str, str] = raw.get("sent", {})
        self.suggested: dict[str, str] = raw.get("suggested", {})

    def seen(self, key: str) -> bool:
        return key in self.sent

    def sent_at(self, key: str) -> datetime | None:
        return datetime.fromisoformat(self.sent[key]) if key in self.sent else None

    def mark(self, key: str, now: datetime):
        self.sent[key] = now.isoformat(timespec="minutes")

    def capped_today(self, today: date) -> int:
        return sum(1 for k, v in self.sent.items() if k.startswith("slot:") and v[:10] == today.isoformat())

    def save(self, today: date):
        cutoff = (today - timedelta(days=30)).isoformat()
        self.sent = {k: v for k, v in self.sent.items() if v[:10] >= cutoff}
        self.path.write_text(json.dumps({"sent": self.sent, "suggested": self.suggested}, indent=1))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def open_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t.get("status") in ("pending", "in_progress")]


def is_routine_todo(t: dict) -> bool:
    return bool(t.get("ttl_days")) and t.get("priority", "low") == "low" and not t.get("remind")


def tier(t: dict) -> str:
    if t.get("remind") in ("critical", "important", "routine"):
        return t["remind"]
    if t.get("priority") == "high":
        return "important"
    if t.get("priority") == "medium" and t.get("due_date") and not t.get("ttl_days"):
        return "important"
    return "routine"


def actionable(t: dict) -> str:
    kind = t.get("actionable") or "anytime"
    return kind if kind in ("business_hours", "anytime", "weekend", "office") else "anytime"


def task_sort_key(t: dict):
    return (PRIORITY_RANK.get(t.get("priority"), 3), str(t.get("due_date") or "9999"))


def esc(s) -> str:
    return html.escape(str(s or ""))


def due_phrase(t: dict, today: date) -> str:
    due = parse_day(t.get("due_date"))
    if not due:
        return ""
    delta = (due - today).days
    if delta < 0:
        return f"{-delta}d overdue"
    return "today" if delta == 0 else "tomorrow" if delta == 1 else f"in {delta}d ({due.strftime('%a %d %b')})"


def task_line(t: dict, today: date) -> str:
    when = due_phrase(t, today)
    flag = "‼️ " if tier(t) == "critical" or t.get("priority") == "high" else ""
    return f"• {flag}{esc(t['title'])} <i>#{t['id']}{' — ' + when if when else ''}</i>"


def task_button_row(t: dict) -> list[tuple[str, str]]:
    title = t["title"] if len(t["title"]) <= 22 else t["title"][:21] + "…"
    if t.get("lapse"):  # meds: snoozing makes no sense — taken or skipped
        return [(f"✓ {title}", f"done:{t['id']}"), ("✗ Skipped", f"cancel:{t['id']}")]
    return [(f"✓ {title}", f"done:{t['id']}"), ("⏭ 1d", f"snooze:{t['id']}:1")]


def day_type(today: date, holidays: dict, periods: list) -> tuple[str, str]:
    """Return (type, reason): 'away' for vacations/blackouts, 'offday' for weekends/holidays."""
    for d1, d2, label, section in periods:
        s = section.lower()
        if d1 <= today <= d2 and ("vacation" in s or "blackout" in s):
            return "away", f"{section}: {label}"
    if today in holidays:
        return "offday", f"Holiday: {holidays[today]}"
    if today.weekday() >= 5:
        return "offday", ""
    return "workday", ""


def wants_ping_today(t: dict, today: date) -> str | None:
    """For an important task: the reason it should be pinged today, or None."""
    due = parse_day(t.get("due_date"))
    if not due:
        return None
    delta = (due - today).days
    if delta == 0:
        return "due"
    if delta > 0 and delta in (t.get("remind_days_before") or []):
        return f"heads-up-{delta}"
    if delta < 0 and -delta in OVERDUE_PING_DAYS and not t.get("lapse"):
        return f"overdue-{-delta}"
    return None


def best_slot(kind: str, dtype: str) -> str | None:
    for slot, kinds in SLOT_KINDS[dtype].items():
        if kind in kinds:
            return slot
    return None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_digest(ctx: dict, state: State) -> str:
    today, cfg = ctx["today"], ctx["cfg"]
    dtype, reason = ctx["dtype"], ctx["dtype_reason"]
    lines = [f"<b>☀️ {today.strftime('%A %d %b')}</b>"]
    if reason:
        lines.append(("🧳 " if dtype == "away" else "🎉 ") + esc(reason))
    for d1, d2, label, section in ctx["periods"]:
        if 0 < (d1 - today).days <= cfg["trip_warning_days"]:
            lines.append(f"🧳 Coming up: {esc(label)} starts {d1.strftime('%a %d %b')} — prep?")

    # Events today
    todays = [e for e in ctx["events"] if parse_day(e.get("start")) == today]
    routine = [e for e in todays if e.get("title") in ctx["routine_titles"]]
    real = sorted((e for e in todays if e not in routine), key=lambda e: str(e.get("start")))
    if real:
        lines.append("\n<b>Today</b>")
        for e in real:
            dt = parse_dt(e.get("start"))
            lines.append(f"• {dt.strftime('%H:%M') if dt else 'all day'} {esc(e['title'])}")
    if routine:
        lines.append(f"<i>+{len(routine)} routine block(s)</i>")

    # Tasks
    stale_cut = today - timedelta(days=cfg["stale_after_days"])
    soon_cut = today + timedelta(days=cfg["lookahead_days"])
    tasks = [t for t in open_tasks(ctx["tasks"]) if not is_routine_todo(t)]
    dated = [(t, parse_day(t.get("due_date"))) for t in tasks]
    dated = [(t, d) for t, d in dated if not (t.get("lapse") and d and d < today)]
    due_today = sorted([t for t, d in dated if d == today], key=task_sort_key)
    overdue = sorted([t for t, d in dated if d and stale_cut <= d < today], key=task_sort_key)
    stale = [t for t, d in dated if d and d < stale_cut]
    soon = sorted([t for t, d in dated if d and today < d <= soon_cut and tier(t) != "routine"
                   or (d and d > today and (d - today).days in (t.get("remind_days_before") or []))],
                  key=task_sort_key)

    for title, group in (("Due today", due_today), ("Overdue", overdue), ("Coming up", soon)):
        if group:
            lines.append(f"\n<b>{title}</b>")
            lines += [task_line(t, today) for t in group]

    routine_todos = [t for t in open_tasks(ctx["tasks"])
                     if is_routine_todo(t)
                     and (d := parse_day(t.get("due_date")) or today) <= today
                     and today <= d + timedelta(days=t["ttl_days"])]
    if routine_todos:
        lines.append(f"\n🔁 Routine: {esc(', '.join(sorted({t['title'] for t in routine_todos})))}")

    # Off-days: nudge the backlog and a goal — the only time undated work surfaces.
    if dtype == "offday":
        lines += offday_suggestions(ctx, state)

    if stale:
        lines.append(f"\n<i>{len(stale)} task(s) overdue >{cfg['stale_after_days']}d — Sunday review will list them.</i>")
    if ctx.get("auth_problem"):
        lines.append(f"\n⚠️ {esc(ctx['auth_problem'])}")
    if len(lines) == 1:
        lines.append("Nothing scheduled or due. 👌")
    return "\n".join(lines)


def offday_suggestions(ctx: dict, state: State) -> list[str]:
    n = ctx["cfg"]["backlog_suggestions"]
    backlog = [t for t in open_tasks(ctx["tasks"])
               if not t.get("due_date") and not is_routine_todo(t)
               and actionable(t) in ("anytime", "weekend", "business_hours")]
    # Least recently suggested first, then priority.
    backlog.sort(key=lambda t: (state.suggested.get(str(t["id"]), ""), task_sort_key(t)))
    picks = backlog[:n]
    out = []
    if picks:
        out.append("\n<b>Good day for</b>")
        out += [f"• {esc(t['title'])} <i>#{t['id']}</i>" for t in picks]
        if not ctx["dry_run"]:
            for t in picks:
                state.suggested[str(t["id"])] = ctx["today"].isoformat()
    goals = [g for g in ctx["goals"] if g["priority"].lower() == "high"]
    if goals:
        g = goals[ctx["today"].toordinal() % len(goals)]
        out.append(f"🎯 Goal time: {esc(g['name'])}" + (f" <i>({esc(g['target'])})</i>" if g["target"] else ""))
    return out


def build_critical(ctx: dict, state: State) -> list[tuple[str, str, list]]:
    """Critical tasks: first ping at remind_at (or first slot), one repeat if unacknowledged."""
    now, today, cfg = ctx["now"], ctx["today"], ctx["cfg"]
    first_slot = min(ctx["slots"].values(), key=hm)
    out = []
    for t in open_tasks(ctx["tasks"]):
        if tier(t) != "critical":
            continue
        due = parse_day(t.get("due_date"))
        if due and due != today:
            continue  # heads-up / overdue days go through the batched slots
        fire = t.get("remind_at") or first_slot
        if (now.hour, now.minute) < hm(fire):
            continue
        k1, k2 = f"crit:{t['id']}:{today}", f"crit2:{t['id']}:{today}"
        if not state.seen(k1):
            out.append((k1, f"🔔 {task_line(t, today)}", [task_button_row(t)]))
        elif not state.seen(k2):
            first = state.sent_at(k1)
            if first and now - first >= timedelta(minutes=cfg["critical_repeat_min"]):
                out.append((k2, f"🔔 Reminder — still open:\n{task_line(t, today)}", [task_button_row(t)]))
    return out


def build_slot(ctx: dict, slot: str) -> tuple[str, str, list] | None:
    """One batched message for this slot: important tasks whose kind fits and that want a ping today."""
    today, dtype = ctx["today"], ctx["dtype"]
    picked = []
    for t in sorted(open_tasks(ctx["tasks"]), key=task_sort_key):
        if best_slot(actionable(t), dtype) != slot:
            continue
        reason = wants_ping_today(t, today)
        if not reason:
            continue
        if tier(t) == "important" or (tier(t) == "critical" and reason != "due"):
            picked.append(t)
    if not picked:
        return None
    text = f"<b>{SLOT_HEADERS.get(slot, slot)}</b>\n" + "\n".join(task_line(t, today) for t in picked)
    return (f"slot:{slot}:{today}", text, [task_button_row(t) for t in picked[:8]])


def build_reminders(ctx: dict) -> list[tuple[str, str, list]]:
    now = ctx["now"]
    out = []
    for e in ctx["events"]:
        start = parse_dt(e.get("start"))
        if not start or e.get("title") in ctx["routine_titles"]:
            continue
        m = DEPART_RE.search(e.get("description") or "")
        if m:
            h, mi = hm(m.group(1))
            fire_at = start.replace(hour=h, minute=mi) - timedelta(minutes=10)
            extra = f"\nDepart by {m.group(1)}."
        else:
            fire_at = start - timedelta(minutes=ctx["cfg"]["event_lead_min"])
            extra = ""
        if fire_at <= now < start:
            where = f" @ {esc(e['location'])}" if e.get("location") else ""
            key = f"remind:{e.get('id') or e.get('external_id')}:{start.isoformat()}"
            out.append((key, f"⏰ <b>{start.strftime('%H:%M')}</b> {esc(e['title'])}{where}{extra}", []))
    return out


def build_review(ctx: dict) -> list[tuple[str, str, list]]:
    today = ctx["today"]
    stale_cut = today - timedelta(days=ctx["cfg"]["stale_after_days"])
    stale = sorted(
        (t for t in open_tasks(ctx["tasks"])
         if not is_routine_todo(t) and (d := parse_day(t.get("due_date"))) and d < stale_cut),
        key=lambda t: str(t.get("due_date")),
    )
    if not stale:
        return []
    out = [(f"review-head:{today}", f"<b>🧹 Weekly review</b> — {len(stale)} stale task(s). Done, push, or drop:", [])]
    for t in stale[:15]:
        out.append((f"review:{t['id']}:{today}", task_line(t, today),
                    [[("✓ Done", f"done:{t['id']}"), ("+1 week", f"snooze:{t['id']}:7"), ("✗ Drop", f"cancel:{t['id']}")]]))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def in_quiet_hours(now: datetime, cfg: dict) -> bool:
    qs, qe = hm(cfg["quiet_start"]), hm(cfg["quiet_end"])
    cur = (now.hour, now.minute)
    return cur >= qs or cur < qe if qs > qe else qs <= cur < qe


def at_or_after(now: datetime, t: str) -> bool:
    return (now.hour, now.minute) >= hm(t)


def check_auth() -> str | None:
    try:
        from google_auth import AuthRequired, SHARED_SCOPES, get_credentials
        app_cfg = load_yaml(CONFIG_PATH)
        get_credentials(Path(app_cfg["gcal"]["credentials"]).expanduser(),
                        Path(app_cfg["gcal"]["token"]).expanduser(), SHARED_SCOPES, interactive=False)
    except AuthRequired:
        return "Google Calendar/Gmail auth expired — run scripts/google_auth.py on the host machine."
    except Exception as e:  # network down etc. — not worth alarming about
        print(f"auth check skipped: {e}", file=sys.stderr)
    return None


def main():
    parser = argparse.ArgumentParser(description="Smart Telegram notifications")
    parser.add_argument("--dry-run", action="store_true", help="Print instead of sending; don't update state")
    parser.add_argument("--at", help="Simulate this time, 'YYYY-MM-DD HH:MM' (use with --dry-run)")
    parser.add_argument("--force", help="Send now regardless of schedule/state: digest, review, or a slot name")
    args = parser.parse_args()

    app_cfg = load_yaml(CONFIG_PATH)
    cfg = {**DEFAULTS, **(app_cfg.get("notify") or {})}
    storage = app_cfg["storage"]
    data_dir = Path(storage["tasks"]).expanduser().parent
    profiles = Path(storage["profiles_dir"]).expanduser()

    now = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=TZ) if args.at else datetime.now(TZ)
    today = now.date()
    holidays, periods = parse_calendar_md(profiles / "calendar.md", today.year)
    dtype, dtype_reason = day_type(today, holidays, periods)
    ctx = {
        "now": now, "today": today, "cfg": cfg, "dry_run": args.dry_run,
        "dtype": dtype, "dtype_reason": dtype_reason, "slots": cfg[f"{dtype}_slots"],
        "tasks": load_yaml(Path(storage["tasks"]).expanduser()).get("tasks", []),
        "events": load_yaml(Path(storage["events"]).expanduser()).get("events", []),
        "routine_titles": {r.get("title", "") for r in
                           load_yaml(Path(storage["recurring_events"]).expanduser()).get("recurring_events", [])},
        "goals": parse_goals_md(profiles / "goals.md"),
        "holidays": holidays, "periods": periods,
    }
    state = State(data_dir / "notify_state.json")

    quiet = in_quiet_hours(now, cfg) and not args.force
    if quiet and not (at_or_after(now, cfg["quiet_start"]) and not at_or_after(now, cfg["critical_until"])):
        return  # fully quiet — between critical_until and quiet_end

    # (key, text, buttons, counts_toward_cap)
    outbox: list[tuple[str, str, list, bool]] = []

    if args.force:
        if args.force == "digest":
            ctx["auth_problem"] = check_auth()
            outbox.append((f"digest:{today}", build_digest(ctx, state), [], False))
        elif args.force == "review":
            outbox += [(*m, False) for m in build_review(ctx)]
        elif msg := build_slot(ctx, args.force):
            outbox.append((*msg, False))
    else:
        slots = ctx["slots"]
        if at_or_after(now, slots["digest"]) and not state.seen(f"digest:{today}"):
            ctx["auth_problem"] = check_auth()
            outbox.append((f"digest:{today}", build_digest(ctx, state), [], False))
        outbox += [(*m, False) for m in build_critical(ctx, state)]
        if quiet:  # late evening: critical only
            outbox = [m for m in outbox if m[0].startswith("crit")]
        outbox += [] if quiet else [(*m, False) for m in build_reminders(ctx)]
        # Only the latest slot that has started — a missed earlier slot rolls forward
        # into the digest/next day rather than firing late.
        started = [s for s, t in slots.items() if at_or_after(now, t)]
        if started and not quiet:
            current = max(started, key=lambda s: hm(slots[s]))
            if msg := build_slot(ctx, current):
                outbox.append((*msg, True))
        if today.weekday() == 6 and at_or_after(now, cfg["review_time"]) and not quiet:
            outbox += [(*m, False) for m in build_review(ctx)]

    for key, text, buttons, capped in outbox:
        if state.seen(key) and not args.force:
            continue
        if capped and state.capped_today(today) >= cfg["daily_cap"]:
            print(f"daily cap reached — skipped {key}", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"--- [{key}]\n{text}" + (f"\n  buttons: {buttons}" if buttons else ""))
            continue
        if send(text, buttons=buttons or None, parse_mode="HTML"):
            state.mark(key, now)

    if not args.dry_run:
        state.save(today)


if __name__ == "__main__":
    main()
