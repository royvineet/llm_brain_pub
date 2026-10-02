---
name: free-time
description: "Goal-aware suggestions when the user has free time ('I have 30 minutes', 'what should I do now?', 'free until 3 pm') — matches available time, energy and location against profiles/tasks.md, goals.md and pending tasks."
---

## Free Time Matching — Goal-aware Suggestions

**When the user says they have free time** (e.g. "I have 30 minutes", "what should I do now?", "I'm free until 3 pm"), run this matching algorithm.

### Inputs to capture
From the user's message, extract:
1. **Available time** — how many minutes (explicit or computed from "free until X")
2. **Current state** — energy/mood: focused, relaxed, tired, energetic, creative (ask if unclear)
3. **Current location** — home, office, outdoors, commuting (infer from time of day + schedule if not stated)

### Matching algorithm

```
1. Read profiles/tasks.md
   → Filter to tasks where Duration ≤ available time
   → Filter to tasks where Energy matches current state
   → Filter to tasks where Location is compatible

2. Read profiles/goals.md
   → Identify active goals with priority high or medium
   → Rank remaining tasks by: related goal priority (high > medium > low > none)

3. Check tasks.yaml
   → Any pending tasks with due dates soon that also fit the time/energy window?
   → These get top priority — real deadlines beat goal work

3a. Check profiles/calendar.md
   → If today is a public holiday or vacation day, prefer leisure/personal tasks over work tasks
   → Note the day type to the user ("Today is a holiday — skipping work tasks")

4. Suggest 2–3 options, ordered by priority:
   - First: any urgent pending task that fits
   - Then: goal-aligned tasks from tasks.md, highest priority goal first
   - Finally: any remaining task that fits the window
```

### Response format
```
You have ~30 min, feeling relaxed, at home. Here's what fits:

1. **Review Spanish flashcards** (15 min, low-energy) — advances "Learn Spanish" [high priority goal]
2. **Sketch practice** (20 min, creative-relaxed) — advances "Learn drawing" [medium priority goal]
3. **Read a chapter** (25 min, focused) — advances "Read 20 books" [medium priority goal]
```

If no tasks match, say so and suggest updating `tasks.md` with activities for this state.
