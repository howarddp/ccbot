"""Memory lifecycle management — listing, cleanup, and summary access.

Provides MemoryManager for high-level memory operations:
  - list_daily(): list recent daily memory files.
  - cleanup(): remove daily memories older than N days.
  - get_summary(): read MEMORY.md long-term memory.

Key class: MemoryManager.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .daily import delete_daily, get_daily
from .db import MemoryDB
from .search import MemorySearchResult

logger = logging.getLogger(__name__)


@dataclass
class DailyMemory:
    """Summary info for a daily memory file."""

    date: str  # YYYY-MM-DD
    size: int  # File size in bytes
    preview: str  # First line of content (truncated)


class MemoryManager:
    """High-level memory lifecycle management."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.memory_dir = workspace_dir / "memory"
        self.db = MemoryDB(workspace_dir)

    def list_daily(self, days: int = 7) -> list[DailyMemory]:
        """List recent daily memory files.

        Args:
            days: Number of recent days to include.

        Returns:
            List of DailyMemory sorted by date (newest first).
        """
        if not self.memory_dir.exists():
            return []

        results: list[DailyMemory] = []
        today = date.today()

        # Collect all .md files that match date format
        for f in sorted(self.memory_dir.glob("*.md"), reverse=True):
            name = f.stem  # e.g. "2026-02-15"
            try:
                file_date = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue

            if (today - file_date).days > days:
                continue

            try:
                content = f.read_text(encoding="utf-8").strip()
                first_line = content.split("\n")[0] if content else ""
                if len(first_line) > 60:
                    first_line = first_line[:57] + "..."
                results.append(
                    DailyMemory(
                        date=name,
                        size=f.stat().st_size,
                        preview=first_line,
                    )
                )
            except OSError:
                continue

        return results

    def get_daily(self, date_str: str) -> str | None:
        """Read a specific daily memory file."""
        return get_daily(self.workspace_dir, date_str)

    def delete_daily(self, date_str: str) -> bool:
        """Delete a specific daily memory file."""
        return delete_daily(self.workspace_dir, date_str)

    def delete_all_daily(self) -> int:
        """Delete all daily memory files. Returns count of deleted files."""
        if not self.memory_dir.exists():
            return 0

        count = 0
        for f in self.memory_dir.glob("*.md"):
            try:
                f.unlink()
                count += 1
            except OSError:
                continue

        logger.info("Deleted %d daily memory files", count)
        return count

    def search(self, query: str) -> list[MemorySearchResult]:
        """Search all memory files for a query string (via SQLite)."""
        rows = self.db.search(query)
        results: list[MemorySearchResult] = []
        for row in rows:
            if row["source"] == "memory_md":
                file = "MEMORY.md"
            else:
                file = f"memory/{row['date']}.md"
            results.append(
                MemorySearchResult(
                    file=file,
                    line_num=row["line_num"],
                    line=row["content"],
                )
            )
        return results

    def get_summary(self) -> str:
        """Read MEMORY.md long-term memory."""
        memory_path = self.workspace_dir / "MEMORY.md"
        try:
            return memory_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def cleanup(self, keep_days: int = 30) -> int:
        """Remove daily memory files older than keep_days.

        Returns:
            Number of files deleted.
        """
        if not self.memory_dir.exists():
            return 0

        cutoff = date.today() - timedelta(days=keep_days)
        count = 0

        for f in self.memory_dir.glob("*.md"):
            try:
                file_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
            except ValueError:
                continue

            if file_date < cutoff:
                try:
                    f.unlink()
                    count += 1
                    logger.debug("Cleaned up old memory: %s", f.name)
                except OSError:
                    continue

        if count:
            logger.info("Cleaned up %d old daily memories (cutoff: %s)", count, cutoff)
        return count
