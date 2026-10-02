#!/usr/bin/env bash
# llm_brain init script — run once after cloning to create the data directory.
#
# What it does:
#   1. Creates ~/Documents/llm_brain/ with the expected layout
#   2. Writes blank tasks.yaml, events.yaml, and index.yaml
#   3. Writes template profile files (fill them in before first use)
#   4. Initialises a git repo in the data directory (for NAS sync via startup.sh)
#
# Usage:
#   bash scripts/init.sh
#   bash scripts/init.sh --data-dir /path/to/custom/dir   # override data location

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Parse optional --data-dir argument ---
DATA_DIR="${HOME}/Documents/llm_brain"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir)
      DATA_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--data-dir PATH]" >&2
      exit 1
      ;;
  esac
done

echo "==> Initialising llm_brain data directory at: ${DATA_DIR}"
echo ""

# --- Create directory structure ---
mkdir -p "${DATA_DIR}/journal"
mkdir -p "${DATA_DIR}/profiles"

# --- tasks.yaml ---
if [ -f "${DATA_DIR}/tasks.yaml" ]; then
  echo "  tasks.yaml already exists — skipping."
else
  cat > "${DATA_DIR}/tasks.yaml" <<'YAML'
tasks: []
YAML
  echo "  Created tasks.yaml"
fi

# --- events.yaml ---
if [ -f "${DATA_DIR}/events.yaml" ]; then
  echo "  events.yaml already exists — skipping."
else
  cat > "${DATA_DIR}/events.yaml" <<'YAML'
events: []
YAML
  echo "  Created events.yaml"
fi

# --- index.yaml ---
if [ -f "${DATA_DIR}/index.yaml" ]; then
  echo "  index.yaml already exists — skipping."
else
  TODAY="$(date +%Y-%m-%d)"
  cat > "${DATA_DIR}/index.yaml" <<YAML
last_updated: "${TODAY}"
journal: []
YAML
  echo "  Created index.yaml"
fi

# ---------------------------------------------------------------------------
# Profile templates — each file is skipped if it already exists
# ---------------------------------------------------------------------------

write_template() {
  local path="$1"
  local content="$2"
  if [ -f "${path}" ]; then
    echo "  $(basename "${path}") already exists — skipping."
  else
    printf '%s\n' "${content}" > "${path}"
    echo "  Created $(basename "${path}")"
  fi
}

write_template "${DATA_DIR}/profiles/individual.md" \
'# Individual Profile

## Identity
- Name:
- Role:
- Employer:

## Locations
- Home:
- Work:

## Schedule
- Work hours:
- Other recurring patterns:

## Preferences
- (Add scheduling or planning preferences here)'

write_template "${DATA_DIR}/profiles/family.md" \
'# Family

## [Member Name]
- Relationship:
- School / Work:
- Schedule:
- Notes:'

write_template "${DATA_DIR}/profiles/environment.md" \
'# Environment

## Key Locations
| Name | Address / Description |
|------|-----------------------|
| Home |                       |
| Work |                       |

## Commute Times
| From | To   | Mode  | Duration | Notes |
|------|------|-------|----------|-------|
| Home | Work |       |          |       |'

write_template "${DATA_DIR}/profiles/goals.md" \
'# Goals

## [Goal Name]
- Area: health | career | learning | creative | financial | relationships
- Why:
- Status: not started | active | paused | achieved
- Priority: high | medium | low
- Work type: focused | creative | physical | low-energy | social
- Activities:
  -
- Target:
- Progress notes:'

write_template "${DATA_DIR}/profiles/tasks.md" \
'# Task Durations & Requirements

## [Category Name]

### [Task Name]
- Duration: X min
- Energy: focused | relaxed | low-energy | energetic | creative | social
- Location: home | office | anywhere | outdoors
- Related goal:
- Notes:'

write_template "${DATA_DIR}/profiles/calendar.md" \
'# Calendar

## Public Holidays

| Date       | Name | Notes |
|------------|------|-------|

## Personal Vacations

| From       | To         | Destination / Label | Notes |
|------------|------------|---------------------|-------|

## Business Travel

| From       | To         | Destination | Notes |
|------------|------------|-------------|-------|

## Work Blackout Periods

| From       | To         | Reason | Notes |
|------------|------------|--------|-------|'

write_template "${DATA_DIR}/profiles/friends.md" \
'# Friends

## [Friend Name]
- How we know each other:
- Relationship:
- Location:
- Family:
- Shared interests:
- Notes:'

write_template "${DATA_DIR}/profiles/reading_list.md" \
'# Reading List

## Format

| Field | Values |
|-------|--------|
| Status | `not-started` · `in-progress` · `paused` · `done` |
| Format | `physical` · `kindle` · `audiobook` · `web` · `pdf` |
| Type | `book` · `article` · `paper` |

---

## Books & Audiobooks

| Title | Author | Status | Format | Notes | Link |
|-------|--------|--------|--------|-------|------|

---

## Articles & Papers

| Title | Source / Author | Status | Format | Notes | Link |
|-------|-----------------|--------|--------|-------|------|'

write_template "${DATA_DIR}/profiles/checklists.md" \
'# Checklists

Reusable templates for recurring travel and preparation scenarios.
When a trip is added, reference the relevant checklist to create one-off tasks.

---

## Business Travel

### 1–2 Weeks Before
- [ ] Book flights
- [ ] Book accommodation
- [ ] Confirm visa / travel documents
- [ ] Share travel dates and itinerary with family
- [ ] Arrange coverage for family responsibilities while away

### 2–3 Days Before
- [ ] Pack clothes and toiletries
- [ ] Carry adapters / chargers
- [ ] Carry work laptop and accessories
- [ ] Check weather at destination
- [ ] Download offline maps / confirm transport from airport

### Day Of
- [ ] Passport and travel documents
- [ ] Local and foreign currency / cards
- [ ] Phone fully charged
- [ ] Confirm accommodation check-in details

### On Return
- [ ] Unpack and do laundry
- [ ] Clear pending work emails / follow-ups from trip

---

## Personal / Family Travel

### 1–2 Weeks Before
- [ ] Book flights / train
- [ ] Book accommodation
- [ ] Check travel documents for all family members
- [ ] Plan rough itinerary and share it with travel companions
- [ ] Arrange any special requirements (dietary, accessibility, kids, etc.)

### 2–3 Days Before
- [ ] Pack for everyone (clothes, toiletries, medications)
- [ ] Pack entertainment / snacks for the journey
- [ ] Carry adapters / chargers
- [ ] Check weather at destination
- [ ] Download offline maps / confirm transport from airport

### Day Of
- [ ] All passports / IDs and travel documents
- [ ] Currency / cards
- [ ] Phones and devices charged
- [ ] Confirm accommodation check-in details
- [ ] Snacks and water for journey

### On Return
- [ ] Unpack and do laundry
- [ ] Catch up on anything deferred before the trip'

write_template "${DATA_DIR}/profiles/directives.md" \
'# Directives

## Guiding Principles
- (Add your scheduling rules, protected time blocks, and hard constraints here)

## Protected Time Blocks
- (e.g. "No work meetings before 9 am")

## Priority Trade-offs
- (e.g. "Family commitments override work tasks at medium priority or below")

## Communication Requirements
- (e.g. "Always confirm before booking anything involving travel > 1 hour")'

echo ""

# --- Git init in data directory ---
if [ -d "${DATA_DIR}/.git" ]; then
  echo "  Git repo already initialised in data directory — skipping."
else
  echo "==> Initialising git repo in data directory..."
  git -C "${DATA_DIR}" init -q
  git -C "${DATA_DIR}" add .
  git -C "${DATA_DIR}" commit -q -m "Initial llm_brain data scaffold"
  echo "  Done. Add your NAS remote with:"
  echo "    git -C \"${DATA_DIR}\" remote add nas <nas-url>"
fi

echo ""
echo "==> Setup complete."
echo ""
echo "Next steps:"
echo "  1. Fill in your profile files in ${DATA_DIR}/profiles/"
echo "  2. (Optional) Add a NAS remote: git -C \"${DATA_DIR}\" remote add nas <url>"
echo "  3. Run scripts/startup.sh to install dependencies and start working"
