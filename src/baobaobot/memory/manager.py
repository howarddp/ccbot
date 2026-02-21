"""Memory lifecycle management — listing, search, and experience access.

Provides MemoryManager for high-level memory operations:
  - list_daily(): list recent daily memory files.
  - search(): search memories via SQLite index.
  - list_experience_files(): list memory/experience/ topic files.

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
from .utils import strip_frontmatter

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
        self.daily_dir = self.memory_dir / "daily"
        self.db = MemoryDB(workspace_dir)

    def list_daily(self, days: int = 7) -> list[DailyMemory]:
        """List recent daily memory files.

        Scans memory/daily/YYYY-MM/YYYY-MM-DD.md files.

        Args:
            days: Number of recent days to include.

        Returns:
            List of DailyMemory sorted by date (newest first).
        """
        if not self.daily_dir.exists():
            return []

        results: list[DailyMemory] = []
        today = date.today()
        cutoff = today - timedelta(days=days)

        for month_dir in sorted(self.daily_dir.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            for f in sorted(month_dir.glob("*.md"), reverse=True):
                # Filename is YYYY-MM-DD.md — stem is the full date
                date_str = f.stem
                try:
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                if file_date < cutoff:
                    continue

                try:
                    raw = f.read_text(encoding="utf-8").strip()
                    content = strip_frontmatter(raw).strip()
                    first_line = content.split("\n")[0] if content else ""
                    if len(first_line) > 60:
                        first_line = first_line[:57] + "..."
                    results.append(
                        DailyMemory(
                            date=date_str,
                            size=f.stat().st_size,
                            preview=first_line,
                        )
                    )
                except OSError:
                    continue

        # Sort by date descending (in case month_dir iteration order wasn't perfect)
        results.sort(key=lambda m: m.date, reverse=True)
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

        Preserves experience/ topic files (long-term memory).
        """
        if not self.daily_dir.exists():
            return 0

        count = 0
        for month_dir in self.daily_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for f in month_dir.glob("*.md"):
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    continue
            # Remove empty month directories
            try:
                month_dir.rmdir()
            except OSError:
                pass

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
                file = f"memory/experience/{row['date']}.md"
            elif row["source"] == "summary":
                file = f"memory/summaries/{row['date']}.md"
            else:
                # Daily: date is 'YYYY-MM-DD', path is 'memory/daily/YYYY-MM/YYYY-MM-DD.md'
                d = row["date"]
                file = f"memory/daily/{d[:7]}/{d}.md"
            results.append(
                MemorySearchResult(
                    file=file,
                    line_num=row["line_num"],
                    line=row["content"],
                )
            )
        return results

    def list_experience_files(self) -> list[str]:
        """List memory/experience/ topic file names (without extension).

        Returns:
            List of topic names sorted alphabetically,
            e.g. ["project-architecture", "user-preferences"].
        """
        exp_dir = self.memory_dir / "experience"
        if not exp_dir.is_dir():
            return []
        return sorted(f.stem for f in exp_dir.glob("*.md"))

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
