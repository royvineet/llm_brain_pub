Process pending documents from the Google Drive `scanned` folder into the NAS archive.

## Workflow

### Step 1 — List pending files
Run:
```bash
.venv/bin/python scripts/docs_helper.py list
```
If the output is an empty array `[]`, report "No documents pending" and stop.

### Step 2 — Read the current taxonomy
Read `~/Documents/llm_brain/doc_taxonomy.yaml` to load the current filing tree and known tags.

### Step 3 — Process each file
For each file in the list:

1. **Ask the user** to describe the document in plain English. Show the filename first.

2. **Classify** based on the description and current taxonomy:
   - Choose a `path` — slash-separated, arbitrary depth (e.g. `finance/tax/me`, `health/<name>/reports`). Stay consistent with the existing tree; add new nodes only when genuinely needed.
   - Choose a `doc_type` — short snake_case (e.g. `itr2_return`, `hospital_bill`, `passport`, `bank_statement`)
   - Determine `doc_date` — YYYY-MM-DD, YYYY-MM, or YYYY; use today if unknown
   - Generate `filename` — format: `DOCDATE_DOCTYPE.EXT` (no spaces, use original extension)
   - Pick `tags` — 3–6 tags, reuse known tags from the taxonomy; add new ones sparingly
   - Write a one-line `description`

3. **Present the classification** and ask the user to confirm or correct it.

4. **File the document** by running:
```bash
.venv/bin/python scripts/docs_helper.py file <file_id> \
  --path "<path>" \
  --doc-type "<doc_type>" \
  --doc-date "<doc_date>" \
  --filename "<filename>" \
  --tags "<tag1,tag2,tag3>" \
  --description "<description>" \
  --original-name "<original filename>"
```

5. Confirm success to the user and move to the next file.

### Step 4 — Summary
After all files are processed, report how many were filed and list them.

## Classification guidelines
- Path depth: be specific but not over-nested. `finance/tax/me` is good; `finance/tax/india/me/2026` is too deep unless the taxonomy already has that structure.
- Filename date prefix: use the document's own date, not today's date.
- When a document belongs to a family member, include their name as a path component (e.g. `health/<name>/`, `identity/<name>/`).
- For shared or household documents, use `family` or omit the person component.
- Suggest a new taxonomy path when you use one — explain briefly why.
