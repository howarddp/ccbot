"""Memory search — plain-text search across all memory files.

Provides grep-style search across daily memory files and MEMORY.md,
returning matching lines with file and line context.

Key class: MemorySearchResult.
Key function: search_memories().
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MemorySearchResult:
    """A single search match in a memory file."""

    file: str  # Relative filename (e.g. "memory/2026-02-15.md" or "MEMORY.md")
    line_num: int
    line: str  # The matching line content


def search_memories(workspace_dir: Path, query: str) -> list[MemorySearchResult]:
    """Search all memory files for a query string (case-insensitive).

    Searches:
      - workspace/MEMORY.md (long-term memory)
      - workspace/memory/*.md (daily memories)

    Returns:
        List of MemorySearchResult sorted by file name (newest first for daily).
    """
    results: list[MemorySearchResult] = []

    try:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
    except re.error:
        logger.warning("Invalid search pattern: %s", query)
        return results

    # Search MEMORY.md
    memory_md = workspace_dir / "MEMORY.md"
    if memory_md.exists():
        _search_file(memory_md, "MEMORY.md", pattern, results)

    # Search daily memory files
    memory_dir = workspace_dir / "memory"
    if memory_dir.exists():
        daily_files = sorted(memory_dir.glob("*.md"), reverse=True)
        for f in daily_files:
            rel = f"memory/{f.name}"
            _search_file(f, rel, pattern, results)

    return results


def _search_file(
    path: Path,
    rel_name: str,
    pattern: re.Pattern[str],
    results: list[MemorySearchResult],
) -> None:
    """Search a single file and append matches to results."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    for i, line in enumerate(content.splitlines(), 1):
        if pattern.search(line):
            results.append(
                MemorySearchResult(file=rel_name, line_num=i, line=line.strip())
            )
