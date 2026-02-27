---
name: cron-history
description: Show cron job execution history. Use when the user asks about past job runs or execution status.
---

Show cron job execution history.

Usage: `{{BIN_DIR}}/cron-history [--job-id ID] [--days N] [--status ok|error] [--limit N]`

Options:
| Flag | Description |
|------|-------------|
| `--job-id ID` | Filter by job ID |
| `--days N` | Show last N days only |
| `--status ok\|error` | Filter by execution status |
| `--limit N` | Max rows (default: 50) |
