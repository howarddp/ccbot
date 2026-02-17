"""Daily memory file management — read/write memory/YYYY-MM-DD.md files.

Each daily memory file captures conversation highlights, decisions, and
observations from a single day. Files are created by Claude Code during
sessions and managed by MemoryManager for lifecycle operations.

Key functions: get_daily(), write_daily(), delete_daily().
"""

import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def _memory_dir(workspace_dir: Path) -> Path:
    """Get the memory directory path."""
    return workspace_dir / "memory"


def _daily_path(workspace_dir: Path, date_str: str) -> Path:
    """Get the path for a specific daily memory file."""
    return _memory_dir(workspace_dir) / f"{date_str}.md"


def get_daily(workspace_dir: Path, date_str: str) -> str | None:
    """Read a specific daily memory file.

    Args:
        workspace_dir: Workspace root path.
        date_str: Date in YYYY-MM-DD format.

    Returns:
        File content, or None if it doesn't exist.
    """
    path = _daily_path(workspace_dir, date_str)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def get_today(workspace_dir: Path) -> str | None:
    """Read today's daily memory file."""
    return get_daily(workspace_dir, date.today().isoformat())


def write_daily(workspace_dir: Path, date_str: str, content: str) -> None:
    """Write content to a daily memory file."""
    mem_dir = _memory_dir(workspace_dir)
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = _daily_path(workspace_dir, date_str)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Wrote daily memory: %s", date_str)


def delete_daily(workspace_dir: Path, date_str: str) -> bool:
    """Delete a specific daily memory file.

    Returns:
        True if the file was deleted, False if it didn't exist.
    """
    path = _daily_path(workspace_dir, date_str)
    try:
        path.unlink()
        logger.info("Deleted daily memory: %s", date_str)
        return True
    except OSError:
        return False
