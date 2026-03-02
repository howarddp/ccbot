---
name: youtube-channel
description: "Browse a YouTube channel's recent videos. Use when: user wants to see what videos a channel has, browse a YouTuber's content, or find recent uploads from a specific channel."
---

# YouTube Channel Skill

Browse a YouTube channel's recent videos. Uses `yt-dlp` via `uvx` (no install needed).

## Usage

```bash
# By @handle
{{BIN_DIR}}/yt-channel "@mkbhd"

# By channel URL
{{BIN_DIR}}/yt-channel "https://www.youtube.com/@mkbhd"

# By name (auto-searches for the channel)
{{BIN_DIR}}/yt-channel "Linus Tech Tips"

# Limit number of videos (default: 10)
{{BIN_DIR}}/yt-channel "@mkbhd" -n 20
```

## When to Use

- User asks "what videos does X have?" or "show me X's latest videos"
- User wants to browse a channel before watching
- User wants to find a specific video from a known channel
- User shares a channel URL and asks about its content

## Output Format

Lists videos with: title, upload date, duration, view count, and URL.

## Notes

- Channel can be specified as @handle, full URL, or name (auto-resolved via search)
- First run may be slow (uvx downloads yt-dlp on first use)
- Default shows 10 most recent videos; use `-n` to adjust
