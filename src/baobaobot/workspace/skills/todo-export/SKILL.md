---
name: todo-export
description: Export TODOs as a Markdown file to tmp/ for sharing with users.
---

Export TODO items as a Markdown file.

**Usage:**
```
{{BIN_DIR}}/todo-export [--status open|done|all] [--type TYPE] [--user USER] [--before DATE] [--after DATE] [--overdue]
```

**Examples:**
```
{{BIN_DIR}}/todo-export                      # Export all open TODOs
{{BIN_DIR}}/todo-export --status all         # Export everything
{{BIN_DIR}}/todo-export --type bug           # Export only bugs
{{BIN_DIR}}/todo-export --overdue            # Export overdue items
```

The exported file is saved to `tmp/todos-export-YYYYMMDD-HHMMSS.md`. Use `[SEND_FILE:/path]` to send it to the user.
