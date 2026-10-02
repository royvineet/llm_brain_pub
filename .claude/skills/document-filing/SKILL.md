---
name: document-filing
description: "Scanned personal documents: filing from Google Drive 'scanned' to the NAS, documents.yaml index, doc_taxonomy.yaml, and finding a filed document (e.g. 'show me ITR2 for FY 26-27', passport, insurance policy)."
---

## Document Organisation

Personal documents are scanned to a Google Drive folder named `scanned`. Use the `/docs` Claude Code skill to process them interactively. The skill:
1. Runs `scripts/docs_helper.py list` to find pending files in Drive
2. Asks the user to describe each document
3. Classifies (path, doc_type, doc_date, filename, tags, description) and asks for confirmation
4. Runs `scripts/docs_helper.py file <id> ...` which downloads, uploads to NAS, indexes in `documents.yaml`, then deletes from Drive (only after all steps succeed)

**Filing paths** are a slash-separated folder hierarchy of arbitrary depth, e.g.:
- `finance/tax/me`, `health/<name>/prescriptions`, `work/contracts`, `travel/family/visa`

The taxonomy is stored in `doc_taxonomy.yaml` — a living YAML tree that grows as documents are filed. The `/docs` skill reads it during classification to stay consistent, and writes new paths/tags back automatically. There are no hardcoded categories.

**Querying documents:** Read `documents.yaml` directly. Filter by `path`, `doc_type`, `doc_date`, or `tags`. Example: "show me ITR2 for FY 26-27" → filter `doc_type: itr2_return` and `doc_date` containing `2026`/`2027`.

**NAS:** host, share, base path and credentials are in `~/Documents/llm_brain/config.yaml` under `nas`.
**First-run Drive auth:** will open a browser to grant Drive scope — one-time only.
