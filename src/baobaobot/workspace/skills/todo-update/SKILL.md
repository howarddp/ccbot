---
name: todo-update
description: Update a TODO item's fields (title, type, deadline, content, status, attachments).
---

Update fields of an existing TODO item.

**Usage:**
```
{{BIN_DIR}}/todo-update ID [--title TEXT] [--type TYPE] [--deadline DATE] [--content TEXT] [--status open|done] [--attach FILE]
```

**Examples:**
```
{{BIN_DIR}}/todo-update T20260225-1 --deadline 2026-03-15
{{BIN_DIR}}/todo-update T20260225-1 --title "Updated title" --type feature
{{BIN_DIR}}/todo-update T20260225-1 --attach /path/to/new-file.pdf
```

- `--attach` appends to existing attachments (does not replace)
