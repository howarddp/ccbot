---
name: todo-add
description: Add a new TODO item. Use for tasks, bugs, features, reminders, or ideas that need tracking.
---

Add a TODO item to the workspace.

**Usage:**
```
{{BIN_DIR}}/todo-add "title" [--type TYPE] [--user NAME] [--start DATE] [--end DATE] [--location TEXT] [--content TEXT] [--attach FILE]
```

- `--end` and `--deadline` are aliases (both accepted)
- Date format: `YYYY-MM-DD` or `YYYY-MM-DD HH:MM` (with time)
- Types: task (default), bug, feature, reminder, idea, event, or any custom string

**Examples:**
```
# Range event (e.g. business trip)
{{BIN_DIR}}/todo-add "台北出差" --type event --user Howard --start 2026-03-01 --end 2026-03-03 --location "台北辦公室"

# Event with specific time
{{BIN_DIR}}/todo-add "看牙醫" --type event --user Howard --start "2026-03-05 14:00" --end "2026-03-05 15:00" --location "台大醫院"

# Regular task with deadline
{{BIN_DIR}}/todo-add "Fix login page crash" --type bug --user Alice --deadline 2026-03-01
```
