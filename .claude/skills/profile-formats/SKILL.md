---
name: profile-formats
description: "Templates and field formats for the profile files (individual.md, family.md, environment.md, goals.md, tasks.md, calendar.md). Use when creating or restructuring a profile file or adding a new section to one."
---

### Profile file format

`individual.md`:
```markdown
# Individual Profile

## Identity
- Name:
- Role:
- Employer:

## Locations
- Home: [neighbourhood or address]
- Work: [address or "remote"]

## Schedule
- Work hours:
- [other recurring patterns]

## Preferences
- [scheduling or planning preferences]
```

`family.md`:
```markdown
# Family

## [Member Name]
- Relationship: [spouse | child | parent | …]
- School / Work: [institution and location]
- Schedule: [e.g. school 8 am – 3 pm, pickup at school gate]
- Notes: [allergies, medical, anything planning-relevant]
```

`environment.md`:
```markdown
# Environment

## Key Locations
| Name | Address / Description |
|------|-----------------------|
| Home | …                     |
| Work | …                     |

## Commute Times
| From | To   | Mode  | Duration | Notes          |
|------|------|-------|----------|----------------|
| Home | Work | Drive | 45 min   | +15 min peak   |
```

`goals.md`:
```markdown
# Goals

## [Goal Name]
- Area: [health | career | learning | creative | financial | relationships | …]
- Why: [one-line motivation]
- Status: [not started | active | paused | achieved]
- Priority: [high | medium | low]
- Work type: [focused | creative | physical | low-energy | social]
- Activities:
  - [specific activity that advances this goal]
  - [another activity]
- Target: [e.g. 30 min/day, 3x/week]
- Progress notes: [optional free text]
```

`tasks.md`:
```markdown
# Task Durations & Requirements

## Category Name

### Task Name
- Duration: X min
- Energy: focused | relaxed | low-energy | energetic | creative | social
- Location: home | office | anywhere | outdoors
- Related goal: (goal name or omit)
- Notes: any context, sequencing rules, or tips
```

`calendar.md`:
```markdown
# Calendar

## Public Holidays

| Date       | Name                  | Notes                          |
|------------|-----------------------|--------------------------------|
| YYYY-MM-DD | Holiday Name          | e.g. national, regional, bank  |

## Personal Vacations

| From       | To         | Destination / Label        | Notes                                  |
|------------|------------|----------------------------|----------------------------------------|
| YYYY-MM-DD | YYYY-MM-DD | e.g. Goa beach trip        | flights booked, hotel: …               |

## Work Blackout Periods

| From       | To         | Reason                     | Notes                                  |
|------------|------------|----------------------------|----------------------------------------|
| YYYY-MM-DD | YYYY-MM-DD | e.g. parental leave        |                                        |
```
