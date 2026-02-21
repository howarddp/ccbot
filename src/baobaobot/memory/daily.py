"""Daily memory file management — read/write memory/daily/YYYY-MM/YYYY-MM-DD.md files.

Each daily memory file captures conversation highlights, decisions, and
observations from a single day. Files are created by Claude Code during
sessions and managed by MemoryManager for lifecycle operations.

Directory structure:
    memory/daily/YYYY-MM/YYYY-MM-DD.md   (e.g. memory/daily/2026-02/2026-02-21.md)

Key functions: get_daily(), write_daily(), delete_daily(), save_attachment(),
               append_to_experience(), migrate_legacy_daily_files().
"""

import logging
import re
import shutil
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


_DAILY_FRONTMATTER_TEMPLATE = """\
---
date: {date}
tags: []
---
"""

_EXPERIENCE_FRONTMATTER_TEMPLATE = """\
---
topic: "{topic}"
tags: []
created: {date}
updated: {date}
---
"""

# Regex to match `updated: YYYY-MM-DD` in YAML frontmatter
_UPDATED_RE = re.compile(r"^(updated:\s*)\d{4}-\d{2}-\d{2}", re.MULTILINE)


def _daily_dir(workspace_dir: Path) -> Path:
    """Get the daily memory directory path (memory/daily/)."""
    return workspace_dir / "memory" / "daily"


def _date_parts(date_str: str) -> tuple[str, str]:
    """Split 'YYYY-MM-DD' into ('YYYY-MM', 'DD').

    >>> _date_parts('2026-02-21')
    ('2026-02', '21')

    Raises ValueError if format is invalid.
    """
    parts = date_str.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {date_str!r}")
    return f"{parts[0]}-{parts[1]}", parts[2]


def _daily_path(workspace_dir: Path, date_str: str) -> Path:
    """Get the path for a specific daily memory file.

    Returns: workspace/memory/daily/YYYY-MM/YYYY-MM-DD.md
    """
    year_month, _day = _date_parts(date_str)
    return _daily_dir(workspace_dir) / year_month / f"{date_str}.md"


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


def _create_daily_with_frontmatter(path: Path, date_str: str) -> None:
    """Create a new daily memory file with YAML frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DAILY_FRONTMATTER_TEMPLATE.format(date=date_str), encoding="utf-8")


def write_daily(workspace_dir: Path, date_str: str, content: str) -> None:
    """Write content to a daily memory file.

    If content doesn't start with frontmatter (---), a frontmatter block is prepended.
    """
    path = _daily_path(workspace_dir, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content.strip() + "\n"
    if not body.startswith("---\n"):
        body = _DAILY_FRONTMATTER_TEMPLATE.format(date=date_str) + body
    path.write_text(body, encoding="utf-8")
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
# NOTE: Constants and helpers below are duplicated in
# workspace/bin/_memory_common.py for standalone bin script use.
# Keep both copies in sync when modifying.

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Matches tmp download prefix: YYYYMMDD_HHMMSS_
_TMP_PREFIX_RE = re.compile(r"^\d{8}_\d{6}_")


def _strip_tmp_prefix(name: str) -> str:
    """Strip the YYYYMMDD_HHMMSS_ prefix added by tmp downloads."""
    return _TMP_PREFIX_RE.sub("", name)


def _attachments_dir(workspace_dir: Path) -> Path:
    """Get the memory attachments directory path."""
    return workspace_dir / "memory" / "attachments"


def append_to_daily(workspace_dir: Path, line: str) -> None:
    """Append a single line to today's daily memory file.

    Creates the file with YAML frontmatter if it doesn't exist.
    """
    today_str = date.today().isoformat()
    path = _daily_path(workspace_dir, today_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _create_daily_with_frontmatter(path, today_str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _copy_to_attachments(
    workspace_dir: Path,
    source_path: Path,
) -> tuple[str, str] | None:
    """Copy a file into memory/attachments/YYYY-MM-DD/ with dedup naming.

    Args:
        workspace_dir: Workspace root path.
        source_path: Absolute path to the source file.

    Returns:
        (rel_path, dest_name) tuple, or None if source doesn't exist.
    """
    if not source_path.is_file():
        logger.warning("_copy_to_attachments: source not found: %s", source_path)
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

    rel_path = f"memory/attachments/{date_str}/{dest_name}"
    return rel_path, dest_name


def _attachment_ref(source_path: Path, description: str, rel_path: str) -> str:
    """Build a Markdown reference for an attachment (image vs file link)."""
    suffix = source_path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return f"![{description}]({rel_path})"
    return f"[{description}]({rel_path})"


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
    result = _copy_to_attachments(workspace_dir, source_path)
    if result is None:
        return None
    rel_path, _ = result

    ref = _attachment_ref(source_path, description, rel_path)
    tag = f"[{user_name}] " if user_name else ""
    append_to_daily(workspace_dir, f"- {tag}{ref}")

    logger.info("Saved attachment: %s -> %s", source_path.name, rel_path)
    return rel_path


# --- Experience support ---
# NOTE: _append_to_experience_file is duplicated in
# workspace/bin/_memory_common.py for standalone bin script use.
# Keep both copies in sync when modifying.


def _experience_heading(topic: str) -> str:
    """Generate a heading from a topic name.

    If the topic looks like kebab-case ASCII (e.g. 'user-preferences'),
    convert to title case ('User Preferences'). Otherwise use as-is
    (e.g. Chinese '使用者偏好' stays unchanged).
    """
    if re.fullmatch(r"[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*", topic):
        return topic.replace("-", " ").title()
    return topic


def _append_to_experience_file(
    workspace_dir: Path,
    topic: str,
    line: str,
) -> str:
    """Append a line to an experience topic file, creating it if needed.

    New files get YAML frontmatter (topic, tags, created, updated) and a heading.
    Existing files get the ``updated`` field bumped to today.

    Args:
        workspace_dir: Workspace root path.
        topic: Topic name (kebab-case or locale language).
        line: The full line to append (without trailing newline).

    Returns:
        Relative path of the experience file.
    """
    exp_dir = workspace_dir / "memory" / "experience"
    exp_dir.mkdir(parents=True, exist_ok=True)
    path = exp_dir / f"{topic}.md"
    today = date.today().isoformat()

    if not path.exists():
        heading = _experience_heading(topic)
        frontmatter = _EXPERIENCE_FRONTMATTER_TEMPLATE.format(
            topic=topic, date=today
        )
        path.write_text(
            f"{frontmatter}# {heading}\n\n{line}\n", encoding="utf-8"
        )
    else:
        content = path.read_text(encoding="utf-8")
        content = _UPDATED_RE.sub(rf"\g<1>{today}", content, count=1)
        if not content.endswith("\n"):
            content += "\n"
        path.write_text(content + line + "\n", encoding="utf-8")

    return f"memory/experience/{topic}.md"


def append_to_experience(
    workspace_dir: Path,
    topic: str,
    content: str,
    user_name: str | None = None,
) -> str:
    """Append content to an experience topic file.

    Creates the file with a heading if it doesn't exist.

    Args:
        workspace_dir: Workspace root path.
        topic: Topic name in kebab-case (e.g. 'user-preferences').
        content: Text content to append.
        user_name: Optional user name to tag.

    Returns:
        Relative path of the experience file.
    """
    tag = f"[{user_name}] " if user_name else ""
    rel = _append_to_experience_file(workspace_dir, topic, f"- {tag}{content}")
    logger.info("Appended to experience: %s", topic)
    return rel


def save_attachment_to_experience(
    workspace_dir: Path,
    source_path: Path,
    description: str,
    topic: str,
    user_name: str | None = None,
) -> str | None:
    """Copy a file into memory/attachments/ and record it in an experience topic file.

    Args:
        workspace_dir: Workspace root path.
        source_path: Absolute path to the source file.
        description: Human-readable description of the attachment.
        topic: Experience topic name in kebab-case.
        user_name: Optional user name to tag.

    Returns:
        Relative path of the saved attachment, or None on failure.
    """
    result = _copy_to_attachments(workspace_dir, source_path)
    if result is None:
        return None
    rel_path, _ = result

    ref = _attachment_ref(source_path, description, rel_path)
    tag = f"[{user_name}] " if user_name else ""
    _append_to_experience_file(workspace_dir, topic, f"- {tag}{ref}")

    logger.info("Saved attachment to experience: %s -> %s", source_path.name, topic)
    return rel_path


# --- Migration ---

# Matches daily memory filename: YYYY-MM-DD.md
_DAILY_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

# Matches old-format DD.md (day-only filename within daily/YYYY-MM/ subdirs)
_DAY_ONLY_RE = re.compile(r"^\d{2}\.md$")


def migrate_legacy_daily_files(workspace_dir: Path) -> int:
    """Move legacy memory/YYYY-MM-DD.md files to memory/daily/YYYY-MM/YYYY-MM-DD.md.

    Also migrates old-format memory/daily/YYYY-MM/DD.md to YYYY-MM-DD.md.

    Returns the number of files migrated.
    """
    memory_dir = workspace_dir / "memory"
    if not memory_dir.is_dir():
        return 0

    migrated = 0

    # Phase 1: memory/YYYY-MM-DD.md → memory/daily/YYYY-MM/YYYY-MM-DD.md
    for f in sorted(memory_dir.glob("*.md")):
        if not _DAILY_FILENAME_RE.match(f.name):
            continue
        date_str = f.stem  # e.g. "2026-02-21"
        new_path = _daily_path(workspace_dir, date_str)
        if new_path.exists():
            logger.warning("Migration skip: %s already exists at %s", f.name, new_path)
            continue
        new_path.parent.mkdir(parents=True, exist_ok=True)
        f.rename(new_path)
        migrated += 1
        logger.info("Migrated daily memory: %s -> %s", f.name, new_path)

    # Phase 2: memory/daily/YYYY-MM/DD.md → memory/daily/YYYY-MM/YYYY-MM-DD.md
    daily_dir = _daily_dir(workspace_dir)
    if daily_dir.is_dir():
        for month_dir in sorted(daily_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for f in sorted(month_dir.glob("*.md")):
                if not _DAY_ONLY_RE.match(f.name):
                    continue
                date_str = f"{month_dir.name}-{f.stem}"
                new_path = month_dir / f"{date_str}.md"
                if new_path.exists():
                    logger.warning(
                        "Migration skip: %s already exists at %s", f.name, new_path
                    )
                    continue
                f.rename(new_path)
                migrated += 1
                logger.info("Migrated daily memory: %s -> %s", f.name, new_path)

    if migrated:
        logger.info("Migrated %d legacy daily memory files", migrated)
    return migrated
