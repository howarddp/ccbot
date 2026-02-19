---
name: memory-save
description: Save a file as a memory attachment. Use when you need to preserve images, documents, or artifacts for future recall.
---

Copy a file to `memory/attachments/` and add a Markdown reference to today's daily memory.

Usage: `{{BIN_DIR}}/memory-save /path/to/file "description"`

Specify user: `{{BIN_DIR}}/memory-save /path/to/file "description" --user Alice`

- Images (`.jpg/.png/.gif/.webp`) use `![description](path)` format
- Other files use `[description](path)` format
- Attachments are cleaned up together with daily memories
