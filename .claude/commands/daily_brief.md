Produce an intelligent, time-aware daily briefing: what's due, what to do, and *when* to do it. Highlight what's time-sensitive, condense the rest, and suggest a sensible order for the day based on the current time, energy windows, and the user's schedule.

## Workflow

### Step 1 — Anchor to the current date AND time
```bash
date "+%Y-%m-%d %H:%M %A"
```
This is the anchor for everything below. The briefing must reflect **how much of the day is left** and **which time-of-day energy windows remain** — a 7 AM brief and a 4 PM brief should look different.

### Step 2 — Read the data
From `~/Documents/llm_brain/`:
- `tasks.yaml` — all tasks
- `events.yaml` — today's events (so suggestions don't collide with meetings)
- `profiles/directives.md` — **read first**; protected time blocks, priority overrides, hard constraints
- `profiles/individual.md` — the user's daily schedule (work hours, routines) to know which windows are free
- `profiles/goals.md` — active goals and their priority (to rank goal-aligned work)
- `profiles/tasks.md` — typical task durations + energy/context fit (use to match tasks to time windows)
- `profiles/calendar.md` — flag if today is a public holiday, vacation, or blackout day

### Step 3 — Select and classify open tasks
Open = `status` is `pending` or `in_progress`.

**Group at the top level by `category`: Work first, then Personal.**

Within each top-level group, **classify into sub-buckets**:
- **Errands** — tasks tagged `errands` (calls, bookings, purchases, admin, finance, family logistics)
- **Career / Hobby** — tasks tagged `hobby`, `study`, `ai`, `tinkering`, `hardware` (deep/creative work)
- **Other / Core** — anything else (e.g. core work tasks)

Within each sub-bucket sort by: `due_date` (soonest/overdue first, `null` last), then `priority` (high → low).

### Step 4 — Decide what to highlight vs condense (this is the intelligence)
Do **not** dump every task. Triage:
- **Highlight (always show, individually):**
  - Anything **overdue** (`due_date` < today) — mark `⚠️ overdue`
  - Anything **due today or within 2 days**
  - Any `priority: high` task, dated or not
  - Any task that fits a **protected block happening today** (per `directives.md`)
- **Condense (summarize, don't enumerate):** undated backlog tasks. Show a one-line count per sub-bucket with 1–2 examples, e.g. *"+ 11 undated work tasks (project backlog) — say `show work backlog` to list."* Don't print all of them.

### Step 5 — Time-aware suggestions ("what to do, when")
This is the differentiator. Using the **current time**, today's **events**, the **directives** (protected blocks), and **tasks.md** (durations + energy fit), propose a light plan for the *remaining* part of the day. Map tasks to the natural energy windows that are still ahead:
- **Morning (focused/energetic):** deep work — core work, high-priority goal work, focused study. Also fitness if it's early.
- **Work hours:** core work tasks and work errands (calls during business hours).
- **Midday/low-energy dips:** quick errands, admin, finance, low-energy calls.
- **Protected blocks from `directives.md`:** the work they protect (defend these).
- **Evening (relaxed):** reading, light study, family logistics.

Honour directives (protected blocks win), avoid clashing with scheduled events, and only suggest tasks whose duration fits the window that's left. Give **2–4 concrete, time-anchored suggestions**, not a full schedule. If it's already late, suggest only what realistically fits before end of day.

### Step 6 — Report format
Concise, bullet-only, no filler. Adapt to time of day.

```
# Daily Brief — <YYYY-MM-DD>, <Weekday> · <HH:MM>
[holiday / vacation / blackout flag if applicable]
[today's events, if any — so suggestions account for them; routine blocks from recurring_events.yaml as a count only]

## ⚠️ Needs attention (overdue / due soon)
- [cat][priority] Title — due <date> ⚠️ overdue

## Work
- Errands: <highlights> (+N condensed)
- Career/Hobby: <highlights>
- Core: <highlights> (+N condensed backlog)

## Personal
- Errands: <highlights> (+N condensed)
- Career/Hobby: <highlights>

## ⏱️ Suggested for the rest of today (<time> onward)
- **<window>** — <task> (<duration>, <energy>) — <why it fits>
- ...
```

Omit any empty section. Keep the whole thing scannable in ~15 seconds — highlight what's time-sensitive, condense the backlog, and make the time-aware suggestions the payoff.

### Step 7 — Sync nudge (read-only)
This skill never commits, pushes, or syncs anything itself — it stays read-only. As the final line only, check whether the data repo has uncommitted changes:
```bash
cd ~/Documents/llm_brain && git status --short
```
If the output is non-empty, append a one-line nudge: `💾 N uncommitted change(s) in your data — run /sync to save & push.` If it's empty, print nothing.
