---
name: web-read
description: "Extract clean text content from web pages. Use when: user shares a URL to read, wants article summaries, or you need to read a web page found via web-search. No API key required."
---

# Web Read Skill

Extract clean, readable text content from any web page. Uses a 2-layer fallback strategy.

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

## Fallback Strategy

The `web-read` script uses trafilatura (fast HTTP fetch + content extraction).
If the script fails (non-zero exit code), fall back to **chrome-devtools MCP**:

### chrome-devtools MCP Fallback

Use the chrome-devtools MCP tools to open the page in a real browser and extract text:

1. Open the URL: `mcp__chrome-devtools__new_page(url, background=true, isolatedContext="web-read")`
2. Extract text: `mcp__chrome-devtools__evaluate_script(function="() => document.body.innerText")`
3. Clean up: `mcp__chrome-devtools__list_pages()` to get page ID, then `mcp__chrome-devtools__close_page(pageId)`

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
- Layer 2 (chrome-devtools) is best for JS-heavy SPAs, bot-protected sites, and pages requiring authentication in the browser
