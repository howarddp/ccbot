---
name: web-search
description: "Search the web via DuckDuckGo. Use when: user asks to look something up, research a topic, find recent news, or needs information beyond your knowledge. No API key required."
---

# Web Search Skill

Search the web using DuckDuckGo. Returns titles, URLs, and snippets.

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

# JSON output
{{BIN_DIR}}/web-search "query" --json
```

## Common Region Codes

- `wt-wt` — No region (default)
- `tw-tzh` — Taiwan (Traditional Chinese)
- `us-en` — United States
- `jp-jp` — Japan

## When to Use

- User asks "search for...", "look up...", "find information about..."
- You need current/recent information beyond your training data
- User wants news about a topic (use `--news`)
- Researching a product, service, or topic
- Fact-checking or verifying information

## Tips

- Use `--region tw-tzh` for Taiwan-specific results
- Use `--time d` or `--time w` for recent results
- Combine with `web-read` to get full article content from search result URLs
