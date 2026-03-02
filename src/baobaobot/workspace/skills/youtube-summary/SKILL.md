---
name: youtube-summary
description: "Summarize YouTube videos by fetching transcripts. Use when: user shares a YouTube link and wants to know what the video is about, get a summary, or extract key points."
---

# YouTube Summary Skill

Fetch YouTube video transcripts and generate summaries. Uses `yt-dlp` via `uvx` (no install needed).

## Usage

```bash
# Basic: fetch transcript with auto language selection
{{BIN_DIR}}/yt-transcript "https://www.youtube.com/watch?v=VIDEO_ID"

# Specify subtitle language
{{BIN_DIR}}/yt-transcript "https://www.youtube.com/watch?v=VIDEO_ID" -l en

# Limit transcript length (default: 30000 chars)
{{BIN_DIR}}/yt-transcript "https://www.youtube.com/watch?v=VIDEO_ID" --max-length 50000
```

## Language Auto-Selection

When no `-l` is specified, subtitles are selected in this priority:
1. Manual subtitles: zh-Hant > zh > en > ja > ko > first available
2. Auto-generated captions: same priority order
3. Fallback: video description if no subtitles exist

## When to Use

- User shares a YouTube URL and asks "what is this about?", "summarize this", "help me understand this video"
- User wants key takeaways or notes from a video
- User asks to save video notes to memory

## Workflow

1. Run `yt-transcript` to fetch the transcript
2. Read the output (title, metadata, transcript text)
3. Summarize based on user's request:
   - **Quick summary**: 3-5 bullet points of key takeaways
   - **Detailed summary**: Section-by-section breakdown with timestamps if available
   - **Specific question**: Answer based on transcript content
4. Optionally save notes to memory with `memory-save`

## Common Language Codes

- `zh-Hant` — Traditional Chinese
- `zh` — Chinese (Simplified)
- `en` — English
- `ja` — Japanese
- `ko` — Korean

## Notes

- First run may be slow (uvx downloads yt-dlp on first use)
- Some videos have no subtitles — the tool falls back to showing the video description
- Very long videos may have transcripts exceeding the default 30000 char limit; use `--max-length` to increase
- Works with standard YouTube URLs, youtu.be short links, and playlist item URLs
