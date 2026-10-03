---
name: recurring
description: "Schemas and rules for recurring_tasks.yaml and recurring_events.yaml — adding/changing anything that repeats: medications, bills/credit-card payments, monthly chores, weekly blocks, every-N-days schedules."
---

## Recurring Tasks — `recurring_tasks.yaml`

Templates for tasks that repeat on a schedule too complex for `events.yaml` (e.g. "2nd Saturday of every month"). At startup, `scripts/generate_recurring_tasks.py` reads this file and creates one-off entries in `tasks.yaml` when an occurrence is due.

**Schema:**
```yaml
recurring_tasks:
  - id: rt1
    title: "Water the plants"
    description: "Optional detail."
    priority: low           # low | medium | high
    category: personal      # work | personal
    tags: [errands, home]
    ttl_days: 7             # auto-expire if not done within N days
    recurrence:
      type: monthly_nth_weekday
      n: 2                  # 2nd occurrence
      weekday: 5            # 0=Monday ... 6=Sunday
    advance_days: 0         # generate task N days before due date (0 = on the day)
    last_generated: null    # updated automatically by the script
    skip_dates:             # optional — suppress generation for specific dates or ranges
      - "2026-05-10"        #   exact date (skip that occurrence)
      - from: "2026-06-01"  #   inclusive date range
        to: "2026-06-15"
```

**Supported recurrence types:**

| Type | Fields | Example |
|------|--------|---------|
| `monthly_nth_weekday` | `n`, `weekday` | 2nd Saturday = `n: 2, weekday: 5` |
| `monthly_fixed_day` | `day` (1–28) | 1st of every month = `day: 1` |
| `weekly` | `weekday` | Every Wednesday = `weekday: 2` |
| `every_n_days` | `every`, `start` | Every alternate night from Oct 2 = `every: 2, start: "2026-10-02"` |

**Rules:**
- Occurrences missed by ≤7 days are still generated on the next startup.
- Occurrences missed by >7 days are skipped silently.
- Generation is idempotent — running startup twice won't create duplicates.
- Generation also runs every 30 min via `scheduled_sync.sh`. Set `advance_days` ≥ the largest `remind_days_before` so the task exists in time for its heads-up.
- Medications live here (not as Google Calendar events) so they get ✓ Taken / ✗ Skipped buttons: `remind: critical`, `remind_at`, `lapse: true`.
- To add a new recurring reminder, append an entry to `recurring_tasks.yaml`.

## Recurring Events — `recurring_events.yaml`

Templates for routine calendar blocks (work blocks, commute). At startup, `scripts/generate_recurring_events.py` writes individual entries into `events.yaml` up to `advance_days` (default 60) ahead, skipping public holidays and blackout periods from `calendar.md`. Deduplicated by title + date.

```yaml
recurring_events:
  - id: re1
    title: "Focus block"
    description: "..."
    tags: [work]
    location: ""
    recurrence:
      type: daily_weekday       # only supported type: Mon–Fri
      time: "10:00"
      duration_min: 60
      weekday_times: {4: "09:00"}   # optional per-weekday override (0=Mon … 4=Fri)
    end_date: "2026-12-31"      # optional — stop generating after this date
    skip_holidays: true
    skip_blackouts: true
    advance_days: 60
    skip_dates: ["2026-04-11", {from: "2026-06-01", to: "2026-06-15"}]
```

- These are **routine** — the notifier never pings for them individually, and briefings should summarise them as a count rather than list each one.
- Prefer a Google Calendar recurring event (`sync_gcal.py --add ... --recurrence`) for anything the user wants on their phone calendar; use this file only for local planning blocks.
- When a template stops applying (job change, project ended), set `end_date` or remove it, and delete its future generated entries from `events.yaml`.
