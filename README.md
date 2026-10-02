# llm_brain

A plain-file personal assistant powered by Claude Code. Manages tasks, calendar events, a daily journal, and personal profiles — all as plain YAML and Markdown on your filesystem.

---

## What it does

- **Tasks** — add, update, and prioritise to-dos; categorised as `work` or `personal`; ephemeral todos auto-purge after completion; recurring tasks auto-generated from templates
- **Events** — track calendar events with automatic commute and schedule enrichment; sync from Google Calendar
- **Journal** — one Markdown file per day; tag entries for fast lookup
- **Profiles** — personal context (identity, family, locations, goals, calendar, reading list, checklists) that Claude reads when scheduling or suggesting activities
- **Search** — hierarchical lookup via an index file so Claude never scans blindly
- **Daily briefing** — ask "what's on today?" for a prioritised summary of tasks (grouped by Work / Personal), events, and morning notes
- **Free-time matching** — tell Claude how much time you have and your energy level; it suggests goal-aligned activities
- **Telegram** — chat with llm_brain from your phone (relayed to `claude -p`), plus smart notifications: morning digest, event reminders, evening nudges, and a Sunday review with one-tap Done/Snooze buttons

---

## Setup

```bash
git clone git@github.com:royvineet/llm_brain_pub.git llm_brain
cd llm_brain
```

**First-time setup** — create the data directory with template files:

```bash
bash scripts/init.sh
```

This creates `~/Documents/llm_brain/` with blank `tasks.yaml`, `events.yaml`, `index.yaml`, template profile files under `profiles/`, and a git repo in the data directory so your data is version-tracked from day one.

To use a different data location:

```bash
bash scripts/init.sh --data-dir /path/to/your/data
```

Then update `config/config.yaml` to match:

```yaml
storage:
  tasks: "/path/to/your/data/tasks.yaml"
  events: "/path/to/your/data/events.yaml"
  journal_dir: "/path/to/your/data/journal"
  profiles_dir: "/path/to/your/data/profiles"
  recurring_tasks: "/path/to/your/data/recurring_tasks.yaml"
```

**Optional: Google Calendar sync**

To pull events from Google Calendar into `events.yaml`, set up OAuth credentials (see below) and configure `config/config.yaml`:

```yaml
gcal:
  credentials: "~/Documents/llm_brain/credentials.json"
  token: "~/Documents/llm_brain/google_token.json"
  calendars:
    - id: "primary"
      label: "personal"
    - id: "<your-family-calendar-id>"
      label: "family"
  sync_days_ahead: 60
```

**Getting OAuth credentials:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project
2. Enable the **Google Calendar API**
3. Create an OAuth 2.0 credential (type: Desktop app) → download as `credentials.json`
4. Place `credentials.json` at the path set in `config.yaml` (default: `~/Documents/llm_brain/credentials.json`)
5. Add your Google account as a **test user** under APIs & Services → OAuth consent screen → Test users
6. Run `.venv/bin/python scripts/google_auth.py` once to complete the browser OAuth flow — one `google_token.json` covers both Calendar and Gmail and is reused on future runs (`--drive` authorizes Drive separately for `/docs`)
7. **Avoid weekly re-auth:** while the consent screen's publishing status is *Testing*, Google expires refresh tokens after 7 days. Click **Publish app** (OAuth consent screen → Audience) — for a personal app you don't need verification; you'll just click through an "unverified app" warning once. `google_auth.py --check` reports token health, and the Telegram digest warns when it expires

To find your family (or other) calendar IDs, run:
```bash
.venv/bin/python -c "
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
creds = Credentials.from_authorized_user_file('~/Documents/llm_brain/google_token.json', ['https://www.googleapis.com/auth/calendar.readonly'])
service = build('calendar', 'v3', credentials=creds)
for c in service.calendarList().list().execute()['items']:
    print(c['id'], c['summary'])
"
```

Fill in the profile files in `~/Documents/llm_brain/profiles/` before your first session — they tell Claude who you are, your schedule, family, locations, and goals.

**Optional: Telegram bot**

To interact with llm_brain via Telegram:

1. Create a bot via [@BotFather](https://t.me/botfather) and copy the token
2. Create `~/Documents/llm_brain/telegram.json`:
   ```json
   {
     "token": "YOUR_BOT_TOKEN",
     "chat_id": null
   }
   ```
3. Run setup to register your chat ID:
   ```bash
   .venv/bin/python scripts/telegram_setup.py
   ```
   Then send any message to your bot on Telegram — the chat ID is saved automatically.
4. Install the background agents (macOS launchd — bot kept alive, notifier every 15 min):
   ```bash
   bash scripts/install_launchd.sh            # --uninstall to remove
   ```
   Logs go to `~/Library/Logs/llm_brain/`.

**Chatting:** every message you send is passed to `claude -p` in this repo; follow-ups within an hour continue the same session (`/new` resets).

**Smart notifications** (`scripts/notify.py`, no LLM involved) — timed around your day instead of firing at random:
- **Day types:** workday, off-day (weekends/holidays), away (vacations). Each has its own slots — e.g. workday digest at 09:00, lunch 13:00, evening 19:30 — and nothing non-urgent lands during work hours except lunch.
- **Tiers:** *critical* (meds, payments due today) ping at a set time and repeat once until ticked ✓; *important* tasks get one batched ✓/Snooze ping in the slot that fits when they can actually be done (`actionable: business_hours` → lunch / errand window, `anytime` → evening, `weekend` → off-days); *routine* stays in the digest.
- **Fatigue controls:** quiet hours, daily cap, heads-up days (`remind_days_before`), overdue items fade out, missed meds lapse (`lapse: true`), third snooze asks "drop or reschedule?", Sunday review of stale tasks.
- **Off-days** also suggest backlog tasks and a goal to work on.

`scripts/scheduled_sync.sh` (every 30 min via launchd) keeps Calendar, Gmail and recurring tasks fresh, so reminders don't depend on running `startup.sh`. Preview any moment without sending:
```bash
.venv/bin/python scripts/notify.py --dry-run --at "2026-10-07 13:00"
```

**Web automation** (`scripts/web.py`): scripted logins to websites in a real Chromium, credentials in the macOS Keychain, 2FA codes requested over Telegram. Keep personal site recipes, scripts and scheduled jobs in `~/Documents/llm_brain/extensions/` (see CLAUDE.md → Personal Extensions) so the repo stays shareable.

One-off message from the CLI:
```bash
.venv/bin/python scripts/telegram_notify.py "Your message here"
```

`telegram.json` is gitignored and lives outside the repo — the token is never committed.

---

**Optional: back up your data to a remote**

The data directory is a plain git repo. Point it at any remote you like:

```bash
# GitHub (private repo recommended), NAS, any git server, etc.
git -C ~/Documents/llm_brain remote add origin <url>
git -C ~/Documents/llm_brain branch --set-upstream-to=origin/main main
```

`startup.sh` will automatically push/pull if a remote is configured, and skip sync silently if there is none.

Run the startup script before each session:

```bash
bash scripts/startup.sh
```

This pulls the latest project code from GitHub, syncs your data with the remote (if configured), rebuilds the index, purges expired todos, and generates any recurring tasks that are due. Then open the project in Claude Code:

```bash
claude
```

---

## Data layout

Data lives outside the repo at the path configured in `config/config.yaml`. Default: `~/Documents/llm_brain/`.

```
~/Documents/llm_brain/
├── index.yaml               # search index — auto-maintained
├── tasks.yaml               # all tasks (work and personal)
├── events.yaml              # calendar events (also populated by gcal sync)
├── recurring_tasks.yaml     # recurring task templates (auto-generates tasks on startup)
├── credentials.json         # Google OAuth client secret (not committed)
├── google_token.json        # Google OAuth token — auto-created on first sync (not committed)
├── telegram.json            # Telegram bot token and chat_id (not committed)
├── journal/                 # YYYY-MM-DD.md per day
└── profiles/
    ├── directives.md   # guiding principles — read first for scheduling
    ├── individual.md   # identity, work, schedule, preferences
    ├── family.md       # family members, schedules, locations
    ├── friends.md      # friends context
    ├── environment.md  # key locations and commute times
    ├── goals.md        # personal goals and progress
    ├── tasks.md        # task-type durations, energy requirements, and category
    ├── calendar.md     # public holidays, vacations, business travel, blackout periods
    ├── reading_list.md # books and articles — read status, format, links
    └── checklists.md   # reusable travel and prep checklists
```

This directory is its own git repo (created by `init.sh`). Changes are tracked locally; push to a remote of your choice for backup.

---

## Task schema

```yaml
- id: 1
  title: "Example task"
  description: ""
  status: pending          # pending | in_progress | done | cancelled
  priority: medium         # low | medium | high
  category: personal       # work | personal
  due_date: "2026-03-15"   # or null
  tags: []
  created_at: "2026-03-13"
  ttl_days: 14             # null for permanent tasks; integer for ephemeral todos
  completed_at: null
```

- **`category`** separates work tasks from personal ones. Daily briefings group tasks by category (Work first, then Personal).
- **`ttl_days`** marks short-lived chores. Completed ephemeral tasks are auto-purged by `purge_todos.py` and summarised in the journal.

---

## Recurring tasks

For tasks that repeat on a schedule too complex for simple recurrence (e.g. "2nd Saturday of every month"), add a template to `recurring_tasks.yaml`:

```yaml
recurring_tasks:
  - id: rt1
    title: Water the plants
    description: Water the balcony plants. Takes ~10 min.
    priority: low
    category: personal
    tags: [errands, home]
    ttl_days: 7
    recurrence:
      type: monthly_nth_weekday
      n: 2        # 2nd occurrence
      weekday: 5  # 0=Monday ... 6=Sunday
    advance_days: 0   # generate task N days before due (0 = on the day)
    last_generated: null
```

`startup.sh` calls `generate_recurring_tasks.py` which creates a one-off entry in `tasks.yaml` when the occurrence is due. Generation is idempotent. Occurrences missed by ≤7 days are still generated on the next startup; older ones are skipped.

**Supported recurrence types:**

| Type | Fields | Example |
|------|--------|---------|
| `monthly_nth_weekday` | `n`, `weekday` | 2nd Saturday = `n: 2, weekday: 5` |

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/init.sh` | One-time setup: create data directory, template profiles, and blank data files |
| `scripts/startup.sh` | Pull latest code, sync data remote (if configured), rebuild index, purge expired todos, generate recurring tasks — run before each session |
| `scripts/reindex.py` | Rebuild `index.yaml` from scratch — called by `startup.sh`, or run manually after bulk file changes |
| `scripts/purge_todos.py` | Remove completed ephemeral tasks past their TTL; append a summary to the journal |
| `scripts/generate_recurring_tasks.py` | Generate due tasks from `recurring_tasks.yaml` templates; idempotent |
| `scripts/sync_check.sh` | Check whether the data directory is in sync with its remote; exits non-zero if behind or diverged |
| `scripts/sync_gcal.py` | Sync Google Calendar events into `events.yaml` — adds new, updates changed, removes deleted (within sync window); run with `--dry-run` to preview |
| `scripts/telegram_bot.py` | Long-polling Telegram bot — relays all incoming messages to `claude -p` and sends the response back |
| `scripts/telegram_notify.py` | Send a one-off Telegram message from CLI or as an imported module |
| `scripts/telegram_setup.py` | One-time setup: waits for you to message the bot and saves your `chat_id` to `telegram.json` |

---

## Example usage

Just talk to Claude Code naturally:

```
Add a high-priority work task to finish the Q1 report by Friday.
What's on my plate today?
Show me just my work tasks.
Log a journal entry: finished the API refactor, blocked on auth review.
I have 45 minutes free — what should I work on?
Find everything I have about the Berlin trip.
What's on my reading list?
/sync
```

---

## Requirements

- [Claude Code](https://claude.ai/code)
- Python 3.10+
- `PyYAML` — installed automatically by `startup.sh` via `requirements.txt`
- `google-auth`, `google-auth-oauthlib`, `google-api-python-client` — required for Google Calendar sync; installed via `requirements.txt`
- `requests` — required for Telegram bot; installed via `requirements.txt`
