"""Shared test helpers for memory tests."""

from pathlib import Path


def daily_file(workspace: Path, date_str: str) -> Path:
    """Get the daily file path in the new directory structure.

    Example: daily_file(ws, "2026-02-15") -> ws/memory/daily/2026-02/15.md
    """
    year_month = date_str[:7]
    day = date_str[8:]
    return workspace / "memory" / "daily" / year_month / f"{day}.md"


def write_daily(workspace: Path, date_str: str, content: str) -> Path:
    """Write a daily file at the new directory structure.

    Creates parent directories as needed. Returns the file path.
    """
    path = daily_file(workspace, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
