"""Shared memory database utilities for standalone bin scripts.

Provides a unified sync and query layer that matches MemoryDB's schema
(content_hash-based change detection, updated_at timestamps, FTS5 search,
tag indexing).

IMPORTANT: Schema (_SCHEMA, _SCHEMA_VERSION) and parsing logic must stay
in sync with ``baobaobot.memory.db.MemoryDB``.  A test in
``tests/baobaobot/memory/test_db.py::TestSchemaSync`` enforces this.
Regex patterns should match ``baobaobot.memory.utils``.

NOTE: Module-level ``_fts_available`` global is acceptable here because
bin scripts are short-lived single-process commands (one DB per run).

Used by: memory-search, memory-list, memory-save
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

# Regex to strip YAML frontmatter (--- ... ---) from the beginning of a file
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)

# Extract tags: [tag1, tag2] from frontmatter
_TAGS_BRACKET_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)

# Inline tags: #word (supports mixed case, normalized to lowercase)
_INLINE_TAG_RE = re.compile(r"(?:^|(?<=\s))#([a-zA-Z][a-zA-Z0-9/-]*)")

# Attachment references: ![desc](path) for images, [desc](path) for files
_ATTACHMENT_RE = re.compile(r"!?\[([^\]]+)\]\(([^)]+)\)")

# Schema version — MUST match baobaobot.memory.db._SCHEMA_VERSION
_SCHEMA_VERSION = 4


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from the beginning of text."""
    return _FRONTMATTER_RE.sub("", text)


def _parse_tags(text: str) -> list[str]:
    """Extract tags from YAML frontmatter and inline #tags.

    Tags are normalized to lowercase.
    Returns sorted list of unique lowercase tag names (without # prefix).
    """
    tags: set[str] = set()

    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        bracket_match = _TAGS_BRACKET_RE.search(fm_match.group(0))
        if bracket_match:
            for t in bracket_match.group(1).split(","):
                t = t.strip().strip('"').strip("'").lstrip("#").lower()
                if t:
                    tags.add(t)

    body = _strip_frontmatter(text)
    for m in _INLINE_TAG_RE.finditer(body):
        tags.add(m.group(1).lower())

    return sorted(tags)


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    line_num    INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS file_meta (
    path        TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    synced_at   TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id'
);

CREATE TABLE IF NOT EXISTS attachment_meta (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_path TEXT    NOT NULL,
    description TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    file_type   TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_memories_date    ON memories(date);
CREATE INDEX IF NOT EXISTS idx_memories_source  ON memories(source);
CREATE INDEX IF NOT EXISTS idx_memories_path    ON memories(path);
CREATE INDEX IF NOT EXISTS idx_attachment_path  ON attachment_meta(memory_path);
"""

# Track FTS5 availability at module level.
# Acceptable for short-lived bin scripts (one DB connection per process).
_fts_available = True


def connect_db(workspace: Path) -> sqlite3.Connection:
    """Open (or create) the memory SQLite database with unified schema."""
    global _fts_available
    db_path = workspace / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < _SCHEMA_VERSION:
        print(
            f"Recreating memory DB (schema v{version} -> v{_SCHEMA_VERSION})",
            file=__import__("sys").stderr,
        )
        conn.executescript(
            "DROP TABLE IF EXISTS memories_fts;\n"
            "DROP TABLE IF EXISTS attachment_meta;\n"
            "DROP TABLE IF EXISTS memories;\n"
            "DROP TABLE IF EXISTS file_meta;\n"
        )
        try:
            conn.executescript(_SCHEMA)
        except sqlite3.OperationalError:
            _fts_available = False
            schema_no_fts = "\n".join(
                line
                for line in _SCHEMA.splitlines()
                if "fts5" not in line.lower()
                and "memories_fts" not in line
                and "content='memories'" not in line
                and "content_rowid" not in line
            )
            conn.executescript(schema_no_fts)
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
    else:
        fts_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories_fts'"
        ).fetchone()
        _fts_available = fts_check is not None

    return conn


def resolve_workspace(args_workspace: str | None = None) -> Path:
    """Resolve workspace directory: explicit arg > cwd (if has memory/) > error."""
    import sys

    if args_workspace:
        return Path(args_workspace)
    cwd = Path.cwd()
    if (cwd / "memory").is_dir():
        return cwd
    print(
        "Cannot determine workspace. Use --workspace or run from a workspace dir.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Sync: .md files → SQLite
# ---------------------------------------------------------------------------


def _file_hash(path: Path) -> str:
    """Fast content hash for change detection."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _needs_sync(conn: sqlite3.Connection, path: Path, rel: str) -> bool:
    """Check whether a file has changed since last sync."""
    row = conn.execute(
        "SELECT content_hash FROM file_meta WHERE path = ?", (rel,)
    ).fetchone()
    if row is None:
        return True
    return row["content_hash"] != _file_hash(path)


def _parse_attachments(content: str) -> list[tuple[str, str, str]]:
    """Parse attachment references from content.

    Returns list of (description, file_path, file_type) tuples.
    Only matches references to memory/attachments/ paths.
    """
    attachments: list[tuple[str, str, str]] = []
    for line in content.splitlines():
        for m in _ATTACHMENT_RE.finditer(line):
            desc = m.group(1)
            fpath = m.group(2)
            if "memory/attachments/" not in fpath:
                continue
            file_type = "image" if m.group(0).startswith("!") else "file"
            attachments.append((desc, fpath, file_type))
    return attachments


def _sync_file(
    conn: sqlite3.Connection,
    path: Path,
    rel: str,
    source: str,
    date_str: str,
) -> None:
    """Index a single .md file into the memories table."""
    current_hash = _file_hash(path)
    now = datetime.now().isoformat()

    conn.execute("DELETE FROM memories WHERE path = ?", (rel,))
    conn.execute("DELETE FROM attachment_meta WHERE memory_path = ?", (rel,))

    try:
        raw_content = path.read_text(encoding="utf-8")
    except OSError:
        return

    # Parse tags from raw content (before stripping frontmatter)
    tags = _parse_tags(raw_content)

    # Strip frontmatter for indexing
    content = _strip_frontmatter(raw_content)

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped:
            conn.execute(
                "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rel, source, date_str, i, stripped, now),
            )

    # Parse and store attachment metadata
    for desc, fpath, ftype in _parse_attachments(content):
        conn.execute(
            "INSERT INTO attachment_meta (memory_path, description, file_path, file_type) "
            "VALUES (?, ?, ?, ?)",
            (rel, desc, fpath, ftype),
        )

    conn.execute(
        "INSERT OR REPLACE INTO file_meta (path, content_hash, synced_at, tags) "
        "VALUES (?, ?, ?, ?)",
        (rel, current_hash, now, json.dumps(tags)),
    )


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS5 index from the memories table."""
    global _fts_available
    if not _fts_available:
        return
    try:
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()
    except sqlite3.OperationalError:
        _fts_available = False


def _migrate_legacy_daily_files(workspace: Path) -> int:
    """Move legacy memory/YYYY-MM-DD.md files to memory/daily/YYYY-MM/YYYY-MM-DD.md.

    Also migrates old-format memory/daily/YYYY-MM/DD.md to YYYY-MM-DD.md.
    """
    memory_dir = workspace / "memory"
    if not memory_dir.is_dir():
        return 0

    daily_re = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
    day_only_re = re.compile(r"^\d{2}\.md$")
    daily_dir = memory_dir / "daily"
    migrated = 0

    # Phase 1: memory/YYYY-MM-DD.md → memory/daily/YYYY-MM/YYYY-MM-DD.md
    for f in sorted(memory_dir.glob("*.md")):
        if not daily_re.match(f.name):
            continue
        date_str = f.stem
        parts = date_str.split("-")
        year_month = f"{parts[0]}-{parts[1]}"
        new_path = daily_dir / year_month / f"{date_str}.md"
        if new_path.exists():
            continue
        new_path.parent.mkdir(parents=True, exist_ok=True)
        f.rename(new_path)
        migrated += 1

    # Phase 2: memory/daily/YYYY-MM/DD.md → memory/daily/YYYY-MM/YYYY-MM-DD.md
    if daily_dir.is_dir():
        for month_dir in sorted(daily_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for f in sorted(month_dir.glob("*.md")):
                if not day_only_re.match(f.name):
                    continue
                date_str = f"{month_dir.name}-{f.stem}"
                new_path = month_dir / f"{date_str}.md"
                if new_path.exists():
                    continue
                f.rename(new_path)
                migrated += 1

    return migrated


def sync_workspace(conn: sqlite3.Connection, workspace: Path) -> int:
    """Sync all memory files to SQLite. Returns number of files synced."""
    # Auto-migrate legacy daily files
    _migrate_legacy_daily_files(workspace)

    memory_dir = workspace / "memory"
    synced = 0

    # Sync experience/ topic files (long-term memory)
    experience_dir = memory_dir / "experience"
    if experience_dir.exists():
        for f in sorted(experience_dir.glob("*.md")):
            rel = f"memory/experience/{f.name}"
            if _needs_sync(conn, f, rel):
                date_str = f.stem
                _sync_file(conn, f, rel, "experience", date_str)
                synced += 1

    # Sync daily files (memory/daily/YYYY-MM/YYYY-MM-DD.md)
    daily_dir = memory_dir / "daily"
    if daily_dir.exists():
        for month_dir in sorted(daily_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for f in sorted(month_dir.glob("*.md")):
                rel = f"memory/daily/{month_dir.name}/{f.name}"
                if _needs_sync(conn, f, rel):
                    # Filename is YYYY-MM-DD.md — stem is the full date
                    date_str = f.stem
                    _sync_file(conn, f, rel, "daily", date_str)
                    synced += 1

    # Sync summary files
    summaries_dir = memory_dir / "summaries"
    if summaries_dir.exists():
        for f in sorted(summaries_dir.glob("*.md")):
            rel = f"memory/summaries/{f.name}"
            if _needs_sync(conn, f, rel):
                date_str = f.stem
                _sync_file(conn, f, rel, "summary", date_str)
                synced += 1

    # Clean up deleted files
    rows = conn.execute("SELECT path FROM file_meta").fetchall()
    for row in rows:
        rel = row["path"]
        full = workspace / rel
        if not full.exists():
            conn.execute("DELETE FROM file_meta WHERE path = ?", (rel,))
            conn.execute("DELETE FROM memories WHERE path = ?", (rel,))
            conn.execute("DELETE FROM attachment_meta WHERE memory_path = ?", (rel,))
            synced += 1

    conn.commit()

    # Rebuild FTS index if anything changed
    if synced:
        _rebuild_fts(conn)

    return synced


def search(
    conn: sqlite3.Connection,
    query: str,
    days: int | None = None,
    tag: str | None = None,
) -> list[sqlite3.Row]:
    """Search memories using FTS5 (with LIKE fallback on error).

    Args:
        conn: SQLite connection (already synced).
        query: Search string.
        days: Optional — limit to daily memories from the last N days.
        tag: Optional — filter by tag name (without # prefix).

    Returns:
        List of Row objects with keys: source, date, line_num, content.
    """
    # FTS5's default tokenizer doesn't handle CJK; use LIKE directly
    # for non-ASCII queries.  For ASCII queries, trust FTS results.
    use_fts = _fts_available and query.isascii()
    if use_fts:
        try:
            return _search_fts(conn, query, days, tag)
        except sqlite3.OperationalError:
            pass

    return _search_like(conn, query, days, tag)


def _search_fts(
    conn: sqlite3.Connection,
    query: str,
    days: int | None,
    tag: str | None,
) -> list[sqlite3.Row]:
    """Search using FTS5 MATCH with BM25 ranking."""
    escaped = query.replace('"', '""')
    fts_query = f'"{escaped}"'

    sql = (
        "SELECT m.source, m.date, m.line_num, m.content "
        "FROM memories_fts fts "
        "JOIN memories m ON m.id = fts.rowid"
    )
    conditions: list[str] = ["memories_fts MATCH ?"]
    params: list[str] = [fts_query]

    if tag is not None:
        sql += " JOIN file_meta fm ON fm.path = m.path"
        conditions.append("fm.tags LIKE ?")
        params.append(f'%"{tag}"%')

    if days is not None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conditions.append("m.source = 'daily'")
        conditions.append("m.date >= ?")
        params.append(cutoff)

    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY fts.rank"

    return conn.execute(sql, params).fetchall()


def _search_like(
    conn: sqlite3.Connection,
    query: str,
    days: int | None,
    tag: str | None,
) -> list[sqlite3.Row]:
    """Search using LIKE (fallback when FTS5 is unavailable)."""
    sql = "SELECT m.source, m.date, m.line_num, m.content FROM memories m"
    conditions: list[str] = ["m.content LIKE ?"]
    params: list[str] = [f"%{query}%"]

    if tag is not None:
        sql += " JOIN file_meta fm ON fm.path = m.path"
        conditions.append("fm.tags LIKE ?")
        params.append(f'%"{tag}"%')

    if days is not None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conditions.append("m.source = 'daily'")
        conditions.append("m.date >= ?")
        params.append(cutoff)

    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY m.date DESC, m.line_num ASC"

    return conn.execute(sql, params).fetchall()


def list_tags(conn: sqlite3.Connection) -> list[str]:
    """Return all unique tags across all indexed files."""
    rows = conn.execute(
        "SELECT tags FROM file_meta WHERE tags != '' AND tags != '[]'"
    ).fetchall()
    all_tags: set[str] = set()
    for row in rows:
        try:
            tags = json.loads(row["tags"])
            all_tags.update(tags)
        except (json.JSONDecodeError, TypeError):
            pass
    return sorted(all_tags)


# ---------------------------------------------------------------------------
# Daily file write utilities (shared by memory-save)
# NOTE: Keep in sync with baobaobot.memory.daily
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Matches tmp download prefix: YYYYMMDD_HHMMSS_
TMP_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}_")

DAILY_FRONTMATTER_TEMPLATE = """\
---
date: {date}
tags: []
---
"""


def daily_file_path(workspace: Path, date_str: str) -> Path:
    """Get path for daily memory file: memory/daily/YYYY-MM/YYYY-MM-DD.md.

    Raises ValueError if date_str is not in YYYY-MM-DD format.
    """
    parts = date_str.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {date_str!r}")
    year_month = f"{parts[0]}-{parts[1]}"
    return workspace / "memory" / "daily" / year_month / f"{date_str}.md"


def ensure_daily_file(workspace: Path, date_str: str) -> Path:
    """Ensure daily memory file exists with frontmatter. Returns path."""
    path = daily_file_path(workspace, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            DAILY_FRONTMATTER_TEMPLATE.format(date=date_str), encoding="utf-8"
        )
    return path


def copy_to_attachments(workspace: Path, source: Path) -> tuple[str, str]:
    """Copy file to memory/attachments/YYYY-MM-DD/ with dedup naming.

    Returns (rel_path, dest_name) tuple.
    """
    att_dir = workspace / "memory" / "attachments"

    # Use local time so date subdir matches date.today()
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_dir = att_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    # Strip tmp timestamp prefix
    clean_name = TMP_TIMESTAMP_RE.sub("", source.name)
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
    shutil.copy2(source, dest)

    rel_path = f"memory/attachments/{date_str}/{dest_name}"
    return rel_path, dest_name


def attachment_ref(source: Path, description: str, rel_path: str) -> str:
    """Build Markdown reference for attachment (image vs file link)."""
    suffix = source.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return f"![{description}]({rel_path})"
    return f"[{description}]({rel_path})"


def append_to_experience_file(workspace: Path, topic: str, line: str) -> str:
    """Append line to experience topic file, creating if needed.

    Returns relative path of the experience file.
    """
    exp_dir = workspace / "memory" / "experience"
    exp_dir.mkdir(parents=True, exist_ok=True)
    path = exp_dir / f"{topic}.md"

    if not path.exists():
        heading = topic.replace("-", " ").title()
        path.write_text(f"# {heading}\n\n{line}\n", encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    return f"memory/experience/{topic}.md"


def format_file_label(row: sqlite3.Row) -> str:
    """Convert a result row's source/date into a human-readable file label."""
    if row["source"] == "experience":
        return f"memory/experience/{row['date']}.md"
    elif row["source"] == "summary":
        return f"memory/summaries/{row['date']}.md"
    else:
        # Daily: date is 'YYYY-MM-DD', path is 'memory/daily/YYYY-MM/YYYY-MM-DD.md'
        d = row["date"]
        return f"memory/daily/{d[:7]}/{d}.md"
