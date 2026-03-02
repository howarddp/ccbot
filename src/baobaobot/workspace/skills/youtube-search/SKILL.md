---
name: youtube-search
description: "Search YouTube videos by keyword. Use when: user wants to find YouTube videos about a topic, search for tutorials, music, reviews, or any content on YouTube."
---

# YouTube Search Skill

Search YouTube videos by keyword. Uses `yt-dlp` via `uvx` (no install needed).

## Usage

```bash
# Basic search (returns 5 results)
{{BIN_DIR}}/yt-search "python tutorial"

# More results
{{BIN_DIR}}/yt-search "react hooks" -n 10

# Sort by date (newest first)
{{BIN_DIR}}/yt-search "typescript 2026" --sort date

# Sort by view count
{{BIN_DIR}}/yt-search "machine learning" --sort views
```

## When to Use

- User asks "find YouTube videos about X" or "search YouTube for X"
- User wants video recommendations on a topic
- User wants to compare videos before watching
- User asks for tutorials, reviews, or educational content on YouTube

## Output Format

Lists videos with: title, channel, upload date, duration, view count, and URL.

## Sort Options

- `relevance` (default) — YouTube's default ranking
- `date` — newest videos first
- `views` — most viewed first

## Notes

- First run may be slow (uvx downloads yt-dlp on first use)
- Default returns 5 results; use `-n` to adjust
- After finding a video, use `youtube-summary` skill to get its transcript/summary
