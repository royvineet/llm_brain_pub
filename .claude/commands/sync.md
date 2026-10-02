Sync Google Calendar into events.yaml, then commit and push pending git changes for both the project repo and the data repo.

## Steps

### 0. Google Calendar sync

Run:
```bash
.venv/bin/python scripts/sync_gcal.py 2>&1 | grep -v FutureWarning | grep -v warnings.warn | grep -v "end of life" | grep -v "update google" | grep -v "NotOpenSSLWarning" | grep -v "OpenSSL" | grep -v "urllib3" | grep -v "python_version"
```

Report the `Sync complete: N added, N updated, N unchanged.` line.
If it fails, report the error and continue with the git steps anyway.

### 1. Project repo (this repository)

Run the following in sequence:

```bash
git status --short
```

If there are staged or unstaged changes:
- Stage all changes: `git add -A`
- Commit with message: `"sync: save changes"` plus today's date (get via `date +%Y-%m-%d`)
- Push: `git push`

If there are no changes, skip and note "Project repo: nothing to commit."

### 2. Data repo (`~/Documents/llm_brain`)

Run the following in sequence:

```bash
cd ~/Documents/llm_brain && git status --short
```

If there are staged or unstaged changes:
- Stage all changes: `git add -A`
- Commit with message: `"sync: save data"` plus today's date
- Push: `git push`

If the push fails because no remote is configured, note "Data repo: no remote configured — committed locally only."

If there are no changes, skip and note "Data repo: nothing to commit."

### 3. Report

After both repos are handled, print a one-line summary for each:
- `Project repo: committed and pushed.` / `nothing to commit.`
- `Data repo: committed and pushed.` / `committed locally (no remote).` / `nothing to commit.`
