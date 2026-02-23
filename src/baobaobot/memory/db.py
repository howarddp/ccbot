"""SQLite-based memory index — sync .md files and query via SQL.

Provides an index layer over the plain-text memory files.  Claude Code
writes ``memory/daily/YYYY-MM/YYYY-MM-DD.md`` (daily) and ``memory/experience/*.md``
(topic-based long-term memory) directly; this module watches for file
changes and keeps a SQLite database in sync so that searches are fast
and structured.

The database lives at ``<workspace>/memory.db``.

Key class: MemoryDB.
"""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .utils import ATTACHMENT_RE, parse_tags, strip_frontmatter

logger = logging.getLogger(__name__)

# Schema version — bump to force DB recreation on next connect.
# IMPORTANT: keep in sync with _memory_common.py (standalone bin scripts).
_SCHEMA_VERSION = 4

# ---------------------------------------------------------------------------
# Dedup helpers — character-bigram Jaccard similarity
# IMPORTANT: keep in sync with _memory_common.py
# ---------------------------------------------------------------------------

_SOURCE_PRIORITY: dict[str, int] = {"experience": 0, "daily": 1, "summary": 2}

_MD_STRIP_RE = re.compile(r"[#*>\[\]()`~_|!-]")


def _char_bigrams(text: str) -> set[str]:
    """Return character bigram set after stripping markdown and whitespace."""
    cleaned = _MD_STRIP_RE.sub("", text)
    cleaned = "".join(cleaned.split())  # collapse whitespace
    if len(cleaned) < 2:
        return set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedup_results(results: list[dict], threshold: float = 0.55) -> list[dict]:
    """Remove near-duplicate search results, keeping higher-priority sources.

    Priority: experience > daily > summary.
    Uses character-bigram Jaccard similarity.
    O(n²) pairwise comparison — fine for typical search result sizes (<200).
    """
    if len(results) <= 1:
        return results

    # Pre-compute bigrams
    bigrams = [_char_bigrams(r["content"]) for r in results]
    keep = [True] * len(results)

    for i in range(len(results)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(results)):
            if not keep[j]:
                continue
            if _jaccard(bigrams[i], bigrams[j]) >= threshold:
                # Drop the lower-priority one
                pri_i = _SOURCE_PRIORITY.get(results[i]["source"], 9)
                pri_j = _SOURCE_PRIORITY.get(results[j]["source"], 9)
                if pri_i <= pri_j:
                    keep[j] = False
                else:
                    keep[i] = False
                    break  # i is dropped, no need to compare further

    return [r for r, k in zip(results, keep) if k]


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL,  -- relative path e.g. 'memory/2026-02-15.md'
    source      TEXT    NOT NULL,  -- 'daily' | 'experience' | 'summary'
    date        TEXT    NOT NULL,  -- 'YYYY-MM-DD' or topic name or 'YYYY-MM-DD_HH00'
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
    memory_path TEXT    NOT NULL,  -- path of the .md file containing the reference
    description TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,  -- relative path of the attachment file
    file_type   TEXT    NOT NULL DEFAULT ''  -- 'image' or 'file'
);

CREATE INDEX IF NOT EXISTS idx_memories_date    ON memories(date);
CREATE INDEX IF NOT EXISTS idx_memories_source  ON memories(source);
CREATE INDEX IF NOT EXISTS idx_memories_path    ON memories(path);
CREATE INDEX IF NOT EXISTS idx_attachment_path  ON attachment_meta(memory_path);
"""


class MemoryDB:
    """SQLite index for workspace memory files."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.memory_dir = workspace_dir / "memory"
        self.daily_dir = self.memory_dir / "daily"
        self.db_path = workspace_dir / "memory.db"
        self._conn: sqlite3.Connection | None = None
        self._fts_available: bool = True
        self._migration_done: bool = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """Open (or return cached) connection and ensure schema exists."""
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema(self._conn)
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create or migrate schema based on version."""
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < _SCHEMA_VERSION:
            logger.info(
                "Recreating memory DB (schema v%d -> v%d), full re-sync will follow",
                version,
                _SCHEMA_VERSION,
            )
            # Drop old tables and recreate
            conn.executescript(
                "DROP TABLE IF EXISTS memories_fts;\n"
                "DROP TABLE IF EXISTS attachment_meta;\n"
                "DROP TABLE IF EXISTS memories;\n"
                "DROP TABLE IF EXISTS file_meta;\n"
            )
            try:
                conn.executescript(_SCHEMA)
            except sqlite3.OperationalError:
                # FTS5 not available — create schema without it
                self._fts_available = False
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
            # Check if FTS5 table exists
            fts_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'memories_fts'"
            ).fetchone()
            self._fts_available = fts_check is not None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Sync: .md files → SQLite
    # ------------------------------------------------------------------

    @staticmethod
    def _file_hash(path: Path) -> str:
        """Fast content hash for change detection."""
        return hashlib.md5(path.read_bytes()).hexdigest()

    def _needs_sync(self, conn: sqlite3.Connection, path: Path, rel: str) -> bool:
        """Check whether a file has changed since last sync."""
        row = conn.execute(
            "SELECT content_hash FROM file_meta WHERE path = ?", (rel,)
        ).fetchone()
        if row is None:
            return True
        return row["content_hash"] != self._file_hash(path)

    @staticmethod
    def _parse_attachments(content: str) -> list[tuple[str, str, str]]:
        """Parse attachment references from content.

        Returns list of (description, file_path, file_type) tuples.
        file_type is 'image' for ![...] or 'file' for [...].
        Only matches references to memory/attachments/ paths.
        """
        attachments: list[tuple[str, str, str]] = []
        for line in content.splitlines():
            for m in ATTACHMENT_RE.finditer(line):
                desc = m.group(1)
                fpath = m.group(2)
                # Only index actual memory attachments
                if "memory/attachments/" not in fpath:
                    continue
                # Check if it's an image reference (match starts with !)
                file_type = "image" if m.group(0).startswith("!") else "file"
                attachments.append((desc, fpath, file_type))
        return attachments

    def _sync_file(
        self,
        conn: sqlite3.Connection,
        path: Path,
        rel: str,
        source: str,
        date: str,
    ) -> None:
        """Index a single .md file into the memories table."""
        current_hash = self._file_hash(path)
        now = datetime.now().isoformat()

        # Remove old rows for this file
        conn.execute("DELETE FROM memories WHERE path = ?", (rel,))
        conn.execute("DELETE FROM attachment_meta WHERE memory_path = ?", (rel,))

        # Read content
        try:
            raw_content = path.read_text(encoding="utf-8")
        except OSError:
            return

        # Parse tags from raw content (before stripping frontmatter)
        tags = parse_tags(raw_content)

        # Strip frontmatter for indexing
        content = strip_frontmatter(raw_content)

        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped:  # skip blank lines
                conn.execute(
                    "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (rel, source, date, i, stripped, now),
                )

        # Parse and store attachment metadata
        for desc, fpath, ftype in self._parse_attachments(content):
            conn.execute(
                "INSERT INTO attachment_meta (memory_path, description, file_path, file_type) "
                "VALUES (?, ?, ?, ?)",
                (rel, desc, fpath, ftype),
            )

        # Update file meta with tags
        conn.execute(
            "INSERT OR REPLACE INTO file_meta (path, content_hash, synced_at, tags) "
            "VALUES (?, ?, ?, ?)",
            (rel, current_hash, now, json.dumps(tags)),
        )

    def sync(self) -> int:
        """Sync all memory files to SQLite.  Returns number of files synced."""
        if not self._migration_done:
            from .daily import migrate_legacy_daily_files

            migrate_legacy_daily_files(self.workspace_dir)
            self._migration_done = True

        conn = self.connect()
        synced = 0

        # Sync experience/ topic files (long-term memory)
        experience_dir = self.memory_dir / "experience"
        if experience_dir.exists():
            for f in sorted(experience_dir.glob("*.md")):
                rel = f"memory/experience/{f.name}"
                if self._needs_sync(conn, f, rel):
                    date_str = f.stem  # e.g. "user-preferences"
                    self._sync_file(conn, f, rel, "experience", date_str)
                    synced += 1

        # Sync daily files (memory/daily/YYYY-MM/YYYY-MM-DD.md)
        if self.daily_dir.exists():
            for month_dir in sorted(self.daily_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for f in sorted(month_dir.glob("*.md")):
                    rel = f"memory/daily/{month_dir.name}/{f.name}"
                    if self._needs_sync(conn, f, rel):
                        # Filename is YYYY-MM-DD.md — stem is the full date
                        date_str = f.stem
                        self._sync_file(conn, f, rel, "daily", date_str)
                        synced += 1

        # Sync summary files
        summaries_dir = self.memory_dir / "summaries"
        if summaries_dir.exists():
            for f in sorted(summaries_dir.glob("*.md")):
                rel = f"memory/summaries/{f.name}"
                if self._needs_sync(conn, f, rel):
                    date_str = f.stem  # e.g. "2026-02-19_1400"
                    self._sync_file(conn, f, rel, "summary", date_str)
                    synced += 1

        # Clean up deleted files
        synced += self._cleanup_deleted(conn)

        conn.commit()

        # Rebuild FTS index if anything changed
        if synced and self._fts_available:
            self._rebuild_fts(conn)

        if synced:
            logger.debug("Synced %d memory files to SQLite", synced)
        return synced

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        """Rebuild the FTS5 index from the memories table."""
        try:
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
            conn.commit()
        except sqlite3.OperationalError:
            self._fts_available = False

    def _cleanup_deleted(self, conn: sqlite3.Connection) -> int:
        """Remove index entries for files that no longer exist on disk."""
        rows = conn.execute("SELECT path FROM file_meta").fetchall()
        cleaned = 0
        for row in rows:
            rel = row["path"]
            full = self.workspace_dir / rel
            if not full.exists():
                conn.execute("DELETE FROM file_meta WHERE path = ?", (rel,))
                conn.execute("DELETE FROM memories WHERE path = ?", (rel,))
                conn.execute(
                    "DELETE FROM attachment_meta WHERE memory_path = ?", (rel,)
                )
                cleaned += 1
        return cleaned

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(
        self, query: str, days: int | None = None, tag: str | None = None
    ) -> list[dict]:
        """Search memories using FTS5 (with LIKE fallback).

        Args:
            query: Search string.
            days: Optional — limit to daily memories from the last N days.
            tag: Optional — filter by tag name (without # prefix).

        Returns:
            List of dicts with keys: source, date, line_num, content.
        """
        self.sync()
        conn = self.connect()

        # FTS5's default tokenizer doesn't handle CJK; use LIKE directly
        # for non-ASCII queries.  For ASCII queries, trust FTS results.
        use_fts = self._fts_available and query.isascii()
        if use_fts:
            try:
                return _dedup_results(self._search_fts(conn, query, days, tag))
            except sqlite3.OperationalError:
                pass

        return _dedup_results(self._search_like(conn, query, days, tag))

    def _search_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        days: int | None,
        tag: str | None,
    ) -> list[dict]:
        """Search using FTS5 MATCH with BM25 ranking."""
        # Phrase search: wrap in quotes for exact substring matching
        escaped = query.replace('"', '""')
        fts_query = f'"{escaped}"'

        sql = (
            "SELECT m.source, m.date, m.line_num, m.content "
            "FROM memories_fts fts "
            "JOIN memories m ON m.id = fts.rowid"
        )
        conditions = ["memories_fts MATCH ?"]
        params: list[str] = [fts_query]

        if tag is not None:
            sql += " JOIN file_meta fm ON fm.path = m.path"
            conditions.append("fm.tags LIKE ?")
            params.append(f'%"{tag}"%')

        if days is not None:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            conditions.append("m.source IN ('daily', 'summary')")
            conditions.append("m.date >= ?")
            params.append(cutoff)

        sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY fts.rank"

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _search_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        days: int | None,
        tag: str | None,
    ) -> list[dict]:
        """Search using LIKE (fallback when FTS5 is unavailable)."""
        sql = "SELECT m.source, m.date, m.line_num, m.content FROM memories m"
        conditions = ["m.content LIKE ?"]
        params: list[str] = [f"%{query}%"]

        if tag is not None:
            sql += " JOIN file_meta fm ON fm.path = m.path"
            conditions.append("fm.tags LIKE ?")
            params.append(f'%"{tag}"%')

        if days is not None:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            conditions.append("m.source IN ('daily', 'summary')")
            conditions.append("m.date >= ?")
            params.append(cutoff)

        sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY m.date DESC, m.line_num ASC"

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_dates(self, days: int = 7) -> list[dict]:
        """List distinct daily memory dates with line counts.

        Returns:
            List of dicts with keys: date, line_count.
        """
        self.sync()
        conn = self.connect()

        rows = conn.execute(
            "SELECT date, COUNT(*) as line_count FROM memories "
            "WHERE source = 'daily' "
            "GROUP BY date ORDER BY date DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_tags(self) -> list[str]:
        """Return all unique tags across all indexed files."""
        self.sync()
        conn = self.connect()

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

    def list_attachments(self, date_str: str | None = None) -> list[dict]:
        """List indexed attachment metadata.

        Args:
            date_str: Optional — filter by daily memory date (YYYY-MM-DD).

        Returns:
            List of dicts with keys: memory_path, description, file_path, file_type.
        """
        self.sync()
        conn = self.connect()

        if date_str:
            year_month = date_str[:7]  # e.g. "2026-02"
            memory_path = f"memory/daily/{year_month}/{date_str}.md"
            rows = conn.execute(
                "SELECT memory_path, description, file_path, file_type "
                "FROM attachment_meta WHERE memory_path = ? "
                "ORDER BY id",
                (memory_path,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT memory_path, description, file_path, file_type "
                "FROM attachment_meta ORDER BY memory_path, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Return memory statistics."""
        self.sync()
        conn = self.connect()

        total_rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        daily_count = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM memories WHERE source = 'daily'"
        ).fetchone()[0]
        experience_count = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM memories WHERE source = 'experience'"
        ).fetchone()[0]
        attachment_count = conn.execute(
            "SELECT COUNT(*) FROM attachment_meta"
        ).fetchone()[0]

        return {
            "total_lines": total_rows,
            "daily_count": daily_count,
            "experience_count": experience_count,
            "attachment_count": attachment_count,
        }
