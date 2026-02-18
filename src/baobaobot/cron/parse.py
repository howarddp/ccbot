"""Parse user input for cron schedule strings.

Supported formats:
  - "0 9 * * *" or "*/5 * * * *" — cron expression (5 fields, quoted)
  - every:30m, every:2h, every:1d  — fixed interval
  - at:2026-02-20T14:00            — one-shot ISO 8601
"""

from __future__ import annotations

import re

from .types import CronSchedule

# Match every:<number><unit> where unit is s/m/h/d
_EVERY_RE = re.compile(r"^every:(\d+)([smhd])$", re.IGNORECASE)

# Match at:<ISO datetime>
_AT_RE = re.compile(r"^at:(.+)$", re.IGNORECASE)

# Match 5-field cron expression (optionally wrapped in quotes)
_CRON_RE = re.compile(
    r'^["\']?'
    r"([*/\d,\-]+\s+[*/\d,\-]+\s+[*/\d,\-]+\s+[*/\d,\-]+\s+[*/\d,\-]+)"
    r'["\']?$'
)

_UNIT_TO_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_schedule(text: str) -> tuple[CronSchedule | None, str]:
    """Parse a schedule string into a CronSchedule.

    Returns (schedule, error_message). On success error_message is empty.
    """
    text = text.strip()
    if not text:
        return None, "Empty schedule string"

    # Try every:<interval>
    m = _EVERY_RE.match(text)
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower()
        seconds = value * _UNIT_TO_SECONDS[unit]
        if seconds <= 0:
            return None, "Interval must be positive"
        return CronSchedule(kind="every", every_seconds=seconds), ""

    # Try at:<datetime>
    m = _AT_RE.match(text)
    if m:
        at_str = m.group(1).strip()
        return CronSchedule(kind="at", at=at_str), ""

    # Try cron expression
    m = _CRON_RE.match(text)
    if m:
        expr = m.group(1).strip()
        return CronSchedule(kind="cron", expr=expr), ""

    return None, f"Unrecognized schedule format: {text}"


def format_schedule(schedule: CronSchedule) -> str:
    """Format a CronSchedule for display."""
    if schedule.kind == "cron":
        tz_part = f" ({schedule.tz})" if schedule.tz else ""
        return f"{schedule.expr}{tz_part}"
    elif schedule.kind == "every":
        return _format_interval(schedule.every_seconds)
    elif schedule.kind == "at":
        return f"at {schedule.at}"
    return f"unknown({schedule.kind})"


def _format_interval(seconds: int) -> str:
    """Format seconds into human-readable interval."""
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"every {seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"every {seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"every {seconds // 60}m"
    return f"every {seconds}s"
