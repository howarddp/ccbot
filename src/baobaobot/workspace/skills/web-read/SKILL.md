---
name: web-read
description: "Extract clean text content from web pages. Use when: user shares a URL to read, wants article summaries, or you need to read a web page found via web-search. No API key required."
---

# Web Read Skill

Extract clean, readable text content from any web page using trafilatura.

## Usage

```bash
# Read a web page (markdown output)
{{BIN_DIR}}/web-read "https://example.com/article"

# Plain text output
{{BIN_DIR}}/web-read "https://example.com/article" --format text

# Include metadata (title, author, date)
{{BIN_DIR}}/web-read "https://example.com/article" --with-metadata

# Limit output length
{{BIN_DIR}}/web-read "https://example.com/article" --length 3000

# XML output (structured)
{{BIN_DIR}}/web-read "https://example.com/article" --format xml
```

## When to Use

- User shares a URL and asks "read this" or "summarize this"
- After `web-search`, read full content of a promising result
- User wants to save a web article to memory
- Extracting recipe, tutorial, or reference material from a page

## Tips

- Use `--with-metadata` to get title, author, and date
- Use `--length 3000` for long articles to avoid overwhelming output
- Combine with `memory-save` to store article summaries
- Combine with `web-search` for research workflows: search → read → summarize
- Works best on articles, blog posts, documentation; may not work well on SPAs or JS-heavy sites
