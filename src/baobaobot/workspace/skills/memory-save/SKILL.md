---
name: memory-save
description: Save a file as a memory attachment. Use when you need to preserve images, documents, or artifacts for future recall.
---

Save text, files, or attachments to BaoBao's memory.

**Text to daily memory:**
```
{{BIN_DIR}}/memory-save "learned something important" --user Alice
```

**Text to experience (long-term):**
```
{{BIN_DIR}}/memory-save -e topic-name "long-term knowledge"
```

**File attachment to daily:**
```
{{BIN_DIR}}/memory-save /path/to/file "description" --user Alice
```

**File attachment to experience:**
```
{{BIN_DIR}}/memory-save -e topic-name /path/to/file "description"
```

- Auto-detects mode: if first argument is an existing file → attachment mode, otherwise → text mode
- `-e TOPIC` / `--experience TOPIC` saves to `memory/experience/<topic>.md` instead of daily
- Images (`.jpg/.png/.gif/.webp`) use `![description](path)` format
- Other files use `[description](path)` format
- Attachments are stored in `memory/attachments/YYYY-MM-DD/`
