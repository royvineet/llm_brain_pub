#!/usr/bin/env python3
"""
Rebuild data/index.yaml by scanning all journal files.

Run this if the index gets out of sync.

Usage:
    python scripts/reindex.py
"""

import re
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent

# Load paths from config
_config = yaml.safe_load((Path.home() / "Documents" / "llm_brain" / "config.yaml").read_text(encoding="utf-8"))
_storage = _config["storage"]
DATA = Path(_storage["journal_dir"]).expanduser().parent
INDEX_PATH = DATA / "index.yaml"


def index_journal() -> list[dict]:
    journal_dir = Path(_storage["journal_dir"]).expanduser()
    entries = []
    for md_file in sorted(journal_dir.glob("????-??-??.md")):
        date_str = md_file.stem
        content = md_file.read_text(encoding="utf-8")

        # Extract tags line at the bottom: "tags: foo, bar"
        tags = []
        tag_match = re.search(r"^tags:\s*(.+)$", content, re.MULTILINE | re.IGNORECASE)
        if tag_match:
            tags = [t.strip() for t in tag_match.group(1).split(",") if t.strip()]

        # Build a brief summary from the first non-heading, non-empty line
        summary_lines = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---") and not line.lower().startswith("tags:"):
                clean = re.sub(r"^#{1,6}\s*", "", line)
                if clean:
                    summary_lines.append(clean)
                if len(summary_lines) >= 3:
                    break
        summary = " | ".join(summary_lines) if summary_lines else ""

        entries.append({"date": date_str, "tags": tags, "summary": summary})

    return entries


def main():
    index = {
        "last_updated": date.today().isoformat(),
        "journal": index_journal(),
    }

    INDEX_PATH.write_text(
        yaml.dump(index, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    print(f"Index rebuilt: {len(index['journal'])} journal entries.")


if __name__ == "__main__":
    main()
