---
name: todo-add
description: Add a new TODO item. Use for tasks, bugs, features, reminders, or ideas that need tracking.
---

Add a TODO item to the workspace.

**Usage:**
```
{{BIN_DIR}}/todo-add "title" [--type TYPE] [--user NAME] [--deadline DATE] [--content TEXT] [--attach FILE]
```

**Types:** task (default), bug, feature, reminder, idea, or any custom string.

**Examples:**
```
{{BIN_DIR}}/todo-add "Fix login page crash" --type bug --user Alice --deadline 2026-03-01
{{BIN_DIR}}/todo-add "Add dark mode" --type feature --content "Support both auto and manual toggle"
{{BIN_DIR}}/todo-add "Pick up medicine" --type reminder --deadline 2026-02-28
{{BIN_DIR}}/todo-add "Review design mockup" --type task --user Bob --attach /path/to/mockup.png
```
