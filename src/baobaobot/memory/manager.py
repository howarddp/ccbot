"""Memory lifecycle management — listing, cleanup, and summary access.

Provides MemoryManager for high-level memory operations:
  - list_daily(): list recent daily memory files.
  - cleanup(): remove daily memories older than N days.
  - get_summary(): read memory/EXPERIENCE.md long-term memory.

Key class: MemoryManager.
"""

import logging
import shutil
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
        """Delete a specific daily memory file, its attachments, and its summaries."""
        self._cleanup_attachments_for_date(date_str)
        self._cleanup_summaries_for_date(date_str)
        return delete_daily(self.workspace_dir, date_str)

    def delete_all_daily(self) -> int:
        """Delete all daily memory files and all attachments. Returns count of deleted files.

        Preserves EXPERIENCE.md (long-term memory).
        """
        if not self.memory_dir.exists():
            return 0

        count = 0
        for f in self.memory_dir.glob("*.md"):
            if f.name == "EXPERIENCE.md":
                continue
            try:
                f.unlink()
                count += 1
            except OSError:
                continue

        # Clean up all attachment subdirectories
        att_dir = self.memory_dir / "attachments"
        if att_dir.exists():
            for d in att_dir.iterdir():
                if d.is_dir():
                    shutil.rmtree(d)

        # Clean up all summaries
        summaries_dir = self.memory_dir / "summaries"
        if summaries_dir.exists():
            for f in summaries_dir.glob("*.md"):
                try:
                    f.unlink()
                except OSError:
                    continue

        logger.info("Deleted %d daily memory files", count)
        return count

    def search(self, query: str) -> list[MemorySearchResult]:
        """Search all memory files for a query string (via SQLite)."""
        rows = self.db.search(query)
        results: list[MemorySearchResult] = []
        for row in rows:
            if row["source"] == "experience":
                file = "memory/EXPERIENCE.md"
            elif row["source"] == "summary":
                file = f"memory/summaries/{row['date']}.md"
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
        """Read memory/EXPERIENCE.md long-term memory."""
        memory_path = self.memory_dir / "EXPERIENCE.md"
        try:
            return memory_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def cleanup(self, keep_days: int = 30) -> int:
        """Remove daily memory files older than keep_days, including their attachments.

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
                self._cleanup_attachments_for_date(f.stem)
                try:
                    f.unlink()
                    count += 1
                    logger.debug("Cleaned up old memory: %s", f.name)
                except OSError:
                    continue

        # Clean up old summaries
        summaries_dir = self.memory_dir / "summaries"
        if summaries_dir.exists():
            for f in summaries_dir.glob("*.md"):
                try:
                    # Parse date from filename: YYYY-MM-DD_HH00.md
                    file_date = datetime.strptime(f.stem[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if file_date < cutoff:
                    try:
                        f.unlink()
                        count += 1
                        logger.debug("Cleaned up old summary: %s", f.name)
                    except OSError:
                        continue

        if count:
            logger.info("Cleaned up %d old daily memories (cutoff: %s)", count, cutoff)
        return count

    def _cleanup_summaries_for_date(self, date_str: str) -> int:
        """Delete summary files for the given date.

        Summaries are stored as memory/summaries/YYYY-MM-DD_HH00.md.
        Returns the number of files deleted.
        """
        summaries_dir = self.memory_dir / "summaries"
        if not summaries_dir.is_dir():
            return 0

        count = 0
        for f in summaries_dir.glob(f"{date_str}_*.md"):
            try:
                f.unlink()
                count += 1
            except OSError:
                continue
        if count:
            logger.debug("Cleaned up %d summaries for %s", count, date_str)
        return count

    def _cleanup_attachments_for_date(self, date_str: str) -> int:
        """Delete the attachment subdirectory for the given date.

        Attachments are stored in memory/attachments/{YYYY-MM-DD}/.
        Returns the number of files deleted.
        """
        date_dir = self.memory_dir / "attachments" / date_str
        if not date_dir.is_dir():
            return 0

        count = sum(1 for f in date_dir.iterdir() if f.is_file())
        shutil.rmtree(date_dir)
        logger.debug("Cleaned up attachment dir: %s (%d files)", date_str, count)
        return count
