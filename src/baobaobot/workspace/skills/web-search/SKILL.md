---
name: web-search
description: "Search the web via DuckDuckGo. Use when: user asks to look something up, research a topic, find recent news, or needs information beyond your knowledge. No API key required."
---

# Web Search Skill

Search the web using DuckDuckGo. Returns titles, URLs, and snippets. Includes retry and chrome-devtools MCP fallback.

## Usage

```bash
# Basic search
{{BIN_DIR}}/web-search "query"

# Limit results
{{BIN_DIR}}/web-search "query" --limit 10

# Taiwan region
{{BIN_DIR}}/web-search "query" --region tw-tzh

# Time-limited search (d=day, w=week, m=month, y=year)
{{BIN_DIR}}/web-search "query" --time w

# News search
{{BIN_DIR}}/web-search "query" --news

# Image search
{{BIN_DIR}}/web-search "query" --images

# Video search
{{BIN_DIR}}/web-search "query" --videos

# JSON output
{{BIN_DIR}}/web-search "query" --json
```

## Fallback Strategy

The script has built-in retry (1 retry with 2s delay on failure).
If the script still fails (non-zero exit code), fall back to **chrome-devtools MCP**:

### chrome-devtools MCP Fallback

Use the chrome-devtools MCP tools to search via Google in a real browser:

1. Open Google search: `mcp__chrome-devtools__new_page(url="https://www.google.com/search?q=<URL_ENCODED_QUERY>", background=true, isolatedContext="web-search")`
2. Extract results: `mcp__chrome-devtools__evaluate_script(function="() => document.body.innerText")`
3. Clean up: `mcp__chrome-devtools__list_pages()` to get page ID, then `mcp__chrome-devtools__close_page(pageId)`

## Common Region Codes

- `wt-wt` — No region (default)
- `tw-tzh` — Taiwan (Traditional Chinese)
- `us-en` — United States
- `jp-jp` — Japan

## When to Use

- User asks "search for...", "look up...", "find information about..."
- You need current/recent information beyond your training data
- User wants news about a topic (use `--news`)
- Finding images of a place or product (use `--images`)
- Finding video content (use `--videos`)

## Tips

- Use `--region tw-tzh` for Taiwan-specific results
- Use `--time d` or `--time w` for recent results
- Combine with `web-read` to get full article content from search result URLs
- `--images` is useful for travel/place research
- `--news`, `--images`, `--videos` are mutually exclusive
