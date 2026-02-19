---
name: cron-add
description: Add a schedule or reminder. Use when the user says "remind me", "every day", "at what time", etc.
---

Add a scheduled task.

Usage: `{{BIN_DIR}}/cron-add "$0" "$1" [--name NAME] [--tz TZ]`

First argument is the schedule, second is the message.

Schedule formats:
| Format | Example | Description |
|--------|---------|-------------|
| `at:<ISO time>` | `at:2026-02-28T09:00` | One-shot, auto-deleted after execution |
| `every:<number><unit>` | `every:30m`, `every:2h`, `every:1d` | Fixed interval (s/m/h/d) |
| cron expression | `"0 9 * * *"`, `"0 9 * * 1-5"` | Standard 5-field cron |

Notes:
- `at:` type is auto-deleted after execution (one-shot reminder)
- After creating a schedule, also save it to memory (e.g. as a TODO) for double assurance
- Timezone defaults to UTC, use `--tz Asia/Taipei` for Taipei time
