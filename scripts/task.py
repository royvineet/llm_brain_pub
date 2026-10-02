#!/usr/bin/env python3
"""
One-command task edits for tasks.yaml — faster and safer than read-then-edit of
a 1000+ line YAML file (each Read/Edit is a model round-trip). Used by Claude,
the Telegram bot's buttons, and anything else that changes a task.

Usage:
    python scripts/task.py add --title "Call dentist" --due 2026-10-09 --priority medium \\
        --category personal --tags personal,health,errands --actionable business_hours
    python scripts/task.py update 42 --due 2026-10-12 --priority high --remind critical
    python scripts/task.py update 42 --clear due,remind_at      # unset fields
    python scripts/task.py done 42 [43 ...]
    python scripts/task.py cancel 42
    python scripts/task.py show 42

Reminder fields (see notify.py): --remind critical|important|routine,
--remind-at HH:MM, --remind-days-before 5,2, --actionable
business_hours|anytime|weekend|office, --lapse.

Every write holds an exclusive lock on tasks.yaml.lock, so concurrent writers
(Claude, bot buttons, generators) don't clobber each other.
"""

import argparse
import fcntl
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import yaml

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"
STANDARD_TAGS = {"work", "personal", "family", "errands", "hobby", "study", "health"}
CHOICES = {
    "status": ("pending", "in_progress", "done", "cancelled"),
    "priority": ("low", "medium", "high"),
    "category": ("work", "personal"),
    "remind": ("critical", "important", "routine"),
    "actionable": ("business_hours", "anytime", "weekend", "office"),
}
FIELD_ORDER = ["id", "title", "description", "status", "priority", "category", "due_date", "tags",
               "created_at", "ttl_days", "completed_at", "remind", "remind_at", "remind_days_before",
               "actionable", "lapse"]


def tasks_path() -> Path:
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    return Path(cfg["storage"]["tasks"]).expanduser()


@contextmanager
def locked_tasks(path: Path | None = None):
    """Yield the parsed tasks document under an exclusive lock; write it back on success."""
    path = path or tasks_path()
    with open(path.with_suffix(".yaml.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {"tasks": []}
        yield doc
        path.write_text(yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")


def find(doc: dict, task_id: int) -> dict:
    for t in doc["tasks"]:
        if t.get("id") == task_id:
            return t
    sys.exit(f"error: task #{task_id} not found")


def summary(t: dict) -> str:
    bits = [t.get("priority", ""), t.get("category", "")]
    if t.get("due_date"):
        bits.append(f"due {t['due_date']}")
    for k in ("remind", "remind_at", "actionable"):
        if t.get(k):
            bits.append(f"{k}={t[k]}")
    if t.get("remind_days_before"):
        bits.append(f"heads-up {t['remind_days_before']}d")
    if t.get("lapse"):
        bits.append("lapse")
    return f"#{t['id']} {t['title']} [{', '.join(b for b in bits if b)}] tags={','.join(t.get('tags') or [])}"


def apply_fields(t: dict, a: argparse.Namespace):
    simple = {"title": a.title, "description": a.description, "priority": a.priority, "category": a.category,
              "due_date": a.due, "remind": a.remind, "remind_at": a.remind_at, "actionable": a.actionable,
              "status": getattr(a, "status", None)}
    for k, v in simple.items():
        if v is not None:
            if k in CHOICES and v not in CHOICES[k]:
                sys.exit(f"error: {k} must be one of {', '.join(CHOICES[k])}")
            t[k] = v
    if a.due:
        date.fromisoformat(a.due)  # validate
    if a.tags is not None:
        t["tags"] = [x.strip() for x in a.tags.split(",") if x.strip()]
    if a.ttl is not None:
        t["ttl_days"] = a.ttl
    if a.remind_days_before is not None:
        t["remind_days_before"] = [int(x) for x in a.remind_days_before.split(",") if x.strip()]
    if a.lapse:
        t["lapse"] = True
    for field in (a.clear or "").split(","):
        field = {"due": "due_date"}.get(field.strip(), field.strip())
        if field in ("due_date", "ttl_days"):
            t[field] = None  # core schema fields stay present, just empty
        elif field:
            t.pop(field, None)
    tags = set(t.get("tags") or [])
    if not tags & STANDARD_TAGS:
        sys.exit(f"error: tags must include at least one of {', '.join(sorted(STANDARD_TAGS))}")
    if "work" in tags and "personal" in tags:
        sys.exit("error: 'work' and 'personal' tags are mutually exclusive")


def ordered(t: dict) -> dict:
    return {k: t[k] for k in FIELD_ORDER if k in t} | {k: v for k, v in t.items() if k not in FIELD_ORDER}


def main():
    parser = argparse.ArgumentParser(description="Edit tasks.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def fields(p, required_title=False):
        p.add_argument("--title", required=required_title)
        p.add_argument("--description")
        p.add_argument("--priority")
        p.add_argument("--category")
        p.add_argument("--due", help="YYYY-MM-DD")
        p.add_argument("--tags", help="comma-separated; must include a standard tag")
        p.add_argument("--ttl", type=int, help="ttl_days for ephemeral todos")
        p.add_argument("--remind")
        p.add_argument("--remind-at", dest="remind_at")
        p.add_argument("--remind-days-before", dest="remind_days_before", help="e.g. 5,2")
        p.add_argument("--actionable")
        p.add_argument("--lapse", action="store_true")
        p.add_argument("--clear", help="comma-separated fields to unset, e.g. due,remind_at")

    fields(sub.add_parser("add"), required_title=True)
    up = sub.add_parser("update")
    up.add_argument("id", type=int)
    up.add_argument("--status")
    fields(up)
    for name in ("done", "cancel", "show"):
        sp = sub.add_parser(name)
        sp.add_argument("ids", type=int, nargs="+")
    a = parser.parse_args()
    today = date.today().isoformat()

    if a.cmd == "show":
        doc = yaml.safe_load(tasks_path().read_text(encoding="utf-8"))
        for i in a.ids:
            print(yaml.dump(find(doc, i), allow_unicode=True, sort_keys=False).strip(), end="\n---\n")
        return

    with locked_tasks() as doc:
        if a.cmd == "add":
            t = {"id": max((x.get("id", 0) for x in doc["tasks"]), default=0) + 1, "description": "",
                 "status": "pending", "priority": "medium", "category": "personal", "due_date": None,
                 "tags": [], "created_at": today, "ttl_days": None, "completed_at": None}
            apply_fields(t, a)
            t = ordered(t)
            doc["tasks"].append(t)
            print(f"Added {summary(t)}")
        elif a.cmd == "update":
            t = find(doc, a.id)
            apply_fields(t, a)
            if t.get("status") in ("done", "cancelled") and not t.get("completed_at"):
                t["completed_at"] = today
            print(f"Updated {summary(t)}")
        else:  # done / cancel
            for i in a.ids:
                t = find(doc, i)
                t["status"] = "done" if a.cmd == "done" else "cancelled"
                t["completed_at"] = today
                print(f"{t['status'].capitalize()}: #{i} {t['title']}")


if __name__ == "__main__":
    main()
