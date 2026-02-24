---
name: todo-list
description: List TODO items. Use when user asks about tasks, pending items, or what needs to be done.
---

List TODO items with optional filters.

**Usage:**
```
{{BIN_DIR}}/todo-list [--status open|done|all] [--type TYPE] [--user USER] [--before DATE] [--after DATE] [--overdue] [--json]
```

**Examples:**
```
{{BIN_DIR}}/todo-list                        # All open TODOs
{{BIN_DIR}}/todo-list --status all           # All TODOs including done
{{BIN_DIR}}/todo-list --type bug             # Only bugs
{{BIN_DIR}}/todo-list --user Alice           # Only Alice's items
{{BIN_DIR}}/todo-list --overdue              # Overdue items
{{BIN_DIR}}/todo-list --json                 # JSON output
```
