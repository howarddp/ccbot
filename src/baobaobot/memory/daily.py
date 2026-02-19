"""Daily memory file management — read/write memory/YYYY-MM-DD.md files.

Each daily memory file captures conversation highlights, decisions, and
observations from a single day. Files are created by Claude Code during
sessions and managed by MemoryManager for lifecycle operations.

Key functions: get_daily(), write_daily(), delete_daily(), save_attachment().
"""

import logging
import re
import shutil
from datetime import date, datetime
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


# --- Attachment support ---

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Matches tmp download prefix: YYYYMMDD_HHMMSS_
_TMP_PREFIX_RE = re.compile(r"^\d{8}_\d{6}_")


def _strip_tmp_prefix(name: str) -> str:
    """Strip the YYYYMMDD_HHMMSS_ prefix added by tmp downloads."""
    return _TMP_PREFIX_RE.sub("", name)


def _attachments_dir(workspace_dir: Path) -> Path:
    """Get the memory attachments directory path."""
    return _memory_dir(workspace_dir) / "attachments"


def append_to_daily(workspace_dir: Path, line: str) -> None:
    """Append a single line to today's daily memory file."""
    mem_dir = _memory_dir(workspace_dir)
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = _daily_path(workspace_dir, date.today().isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def save_attachment(
    workspace_dir: Path,
    source_path: Path,
    description: str,
    user_name: str | None = None,
) -> str | None:
    """Copy a file into memory/attachments/ and record it in today's daily memory.

    Args:
        workspace_dir: Workspace root path.
        source_path: Absolute path to the source file.
        description: Human-readable description of the attachment.
        user_name: Optional user name to tag in the memory entry.

    Returns:
        Relative path (from workspace) of the saved attachment, or None on failure.
    """
    if not source_path.is_file():
        logger.warning("save_attachment: source not found: %s", source_path)
        return None

    att_dir = _attachments_dir(workspace_dir)

    # Use local time so date subdir matches date.today() used for daily .md filenames.
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_dir = att_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    # Strip tmp timestamp prefix (YYYYMMDD_HHMMSS_) and use clean filename
    clean_name = _strip_tmp_prefix(source_path.name)
    dest_name = clean_name
    dest = date_dir / dest_name
    if dest.exists():
        stem = Path(clean_name).stem
        ext = Path(clean_name).suffix
        n = 2
        while dest.exists():
            dest_name = f"{stem}_{n}{ext}"
            dest = date_dir / dest_name
            n += 1
    shutil.copy2(source_path, dest)

    # Build relative path from workspace root
    rel_path = f"memory/attachments/{date_str}/{dest_name}"

    # Build Markdown reference
    suffix = source_path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        ref = f"![{description}]({rel_path})"
    else:
        ref = f"[{description}]({rel_path})"

    # Build the memory line
    tag = f"[{user_name}] " if user_name else ""
    line = f"- {tag}{ref}"
    append_to_daily(workspace_dir, line)

    logger.info("Saved attachment: %s -> %s", source_path.name, rel_path)
    return rel_path
