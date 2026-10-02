#!/usr/bin/env python3
"""
Purge ephemeral tasks from tasks.yaml.

Ephemeral tasks have a `ttl_days` field set (integer).

- done: purged when completed_at + ttl_days <= today.
  Summarised into the journal entry for their completion date.
- cancelled: purged when created_at + ttl_days <= today (no completed_at needed).
  Silently removed — no journal entry.
- pending/in_progress: flagged with a warning but NOT deleted — they may
  still need doing.

Usage:
    python scripts/purge_todos.py
"""

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
_config = yaml.safe_load((Path.home() / "Documents" / "llm_brain" / "config.yaml").read_text(encoding="utf-8"))
_storage = _config["storage"]

TASKS_PATH = Path(_storage["tasks"]).expanduser()
JOURNAL_DIR = Path(_storage["journal_dir"]).expanduser()


def load_tasks() -> list[dict]:
    if not TASKS_PATH.exists():
        return []
    data = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8")) or {}
    return data.get("tasks", [])


def save_tasks(tasks: list[dict]) -> None:
    TASKS_PATH.write_text(
        yaml.dump(
            {"tasks": tasks},
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def append_to_journal(entry_date: date, titles: list[str]) -> None:
    journal_path = JOURNAL_DIR / f"{entry_date.isoformat()}.md"
    summary_line = "Completed: " + ", ".join(titles)

    if journal_path.exists():
        content = journal_path.read_text(encoding="utf-8")
        if "\n---\ntags:" in content:
            content = content.replace(
                "\n---\ntags:",
                f"\n## Todos\n{summary_line}\n\n---\ntags:",
            )
        else:
            content = content.rstrip("\n") + f"\n\n## Todos\n{summary_line}\n"
        journal_path.write_text(content, encoding="utf-8")
    else:
        journal_path.write_text(
            f"# {entry_date.isoformat()}\n\n## Todos\n{summary_line}\n\n---\ntags: todos\n",
            encoding="utf-8",
        )
    print(f"  Journal {entry_date.isoformat()}: logged {len(titles)} completed todo(s)")


def main() -> None:
    tasks = load_tasks()
    today = date.today()

    to_purge: list[dict] = []
    to_keep: list[dict] = []
    stale_pending: list[dict] = []

    for task in tasks:
        ttl = task.get("ttl_days")
        if ttl is None:
            # Permanent task — never purge
            to_keep.append(task)
            continue

        status = task.get("status", "pending")
        created_at_raw = task.get("created_at")

        if status == "done":
            completed_at_raw = task.get("completed_at")
            if completed_at_raw:
                completed_at = date.fromisoformat(str(completed_at_raw))
                if today >= completed_at + timedelta(days=int(ttl)):
                    to_purge.append(task)
                    continue
        elif status == "cancelled":
            # Use created_at as the reference date since cancelled tasks have no completed_at
            if created_at_raw:
                created_at = date.fromisoformat(str(created_at_raw))
                if today >= created_at + timedelta(days=int(ttl)):
                    to_purge.append(task)
                    continue
        else:
            # Pending/in_progress ephemeral task — warn if stale
            if created_at_raw:
                created_at = date.fromisoformat(str(created_at_raw))
                if today >= created_at + timedelta(days=int(ttl)):
                    stale_pending.append(task)

        to_keep.append(task)

    if stale_pending:
        print(f"WARNING: {len(stale_pending)} ephemeral todo(s) still pending after their TTL:")
        for t in stale_pending:
            print(f"  - [{t['id']}] {t['title']} (created {t.get('created_at', '?')}, ttl {t.get('ttl_days')}d)")

    if not to_purge:
        print("No todos to purge.")
        return

    # Only done tasks get a journal entry; cancelled are removed silently
    done_tasks = [t for t in to_purge if t.get("status") == "done"]
    cancelled_tasks = [t for t in to_purge if t.get("status") == "cancelled"]

    if done_tasks:
        by_date: dict[date, list[str]] = defaultdict(list)
        for t in done_tasks:
            d = date.fromisoformat(str(t["completed_at"]))
            by_date[d].append(t["title"])

        print(f"Purging {len(done_tasks)} completed todo(s)...")
        for entry_date in sorted(by_date):
            append_to_journal(entry_date, by_date[entry_date])

    if cancelled_tasks:
        print(f"Purging {len(cancelled_tasks)} cancelled todo(s) (no journal entry):")
        for t in cancelled_tasks:
            print(f"  - [{t['id']}] {t['title']}")

    save_tasks(to_keep)
    print(f"Done. {len(to_keep)} task(s) remain.")


if __name__ == "__main__":
    main()
