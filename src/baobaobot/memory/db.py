"""SQLite-based memory index — sync .md files and query via SQL.

Provides an index layer over the plain-text memory files.  Claude Code
continues to write ``memory/YYYY-MM-DD.md`` and ``MEMORY.md`` directly;
this module watches for file changes and keeps a SQLite database in sync
so that searches are fast and structured.

The database lives at ``<workspace>/memory.db``.

Key class: MemoryDB.
"""

import hashlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,  -- 'daily' | 'memory_md' | 'summary'
    date        TEXT    NOT NULL,  -- 'YYYY-MM-DD' or 'YYYY-MM-DD_HH00' or 'longterm'
    line_num    INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS file_meta (
    path        TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    synced_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_date    ON memories(date);
CREATE INDEX IF NOT EXISTS idx_memories_source  ON memories(source);
"""


class MemoryDB:
    """SQLite index for workspace memory files."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.memory_dir = workspace_dir / "memory"
        self.db_path = workspace_dir / "memory.db"
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """Open (or return cached) connection and ensure schema exists."""
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        return self._conn

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
        conn.execute(
            "DELETE FROM memories WHERE source = ? AND date = ?", (source, date)
        )

        # Insert lines
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return

        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped:  # skip blank lines
                conn.execute(
                    "INSERT INTO memories (source, date, line_num, content, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source, date, i, stripped, now),
                )

        # Update file meta
        conn.execute(
            "INSERT OR REPLACE INTO file_meta (path, content_hash, synced_at) "
            "VALUES (?, ?, ?)",
            (rel, current_hash, now),
        )

    def sync(self) -> int:
        """Sync all memory files to SQLite.  Returns number of files synced."""
        conn = self.connect()
        synced = 0

        # Sync MEMORY.md
        memory_md = self.workspace_dir / "MEMORY.md"
        if memory_md.exists() and self._needs_sync(conn, memory_md, "MEMORY.md"):
            self._sync_file(conn, memory_md, "MEMORY.md", "memory_md", "longterm")
            synced += 1

        # Sync daily files
        if self.memory_dir.exists():
            for f in sorted(self.memory_dir.glob("*.md")):
                rel = f"memory/{f.name}"
                if self._needs_sync(conn, f, rel):
                    date_str = f.stem  # e.g. "2026-02-15"
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
        if synced:
            logger.debug("Synced %d memory files to SQLite", synced)
        return synced

    def _cleanup_deleted(self, conn: sqlite3.Connection) -> int:
        """Remove index entries for files that no longer exist on disk."""
        rows = conn.execute("SELECT path FROM file_meta").fetchall()
        cleaned = 0
        for row in rows:
            rel = row["path"]
            full = self.workspace_dir / rel
            if not full.exists():
                conn.execute("DELETE FROM file_meta WHERE path = ?", (rel,))
                # Determine source/date from rel path
                if rel == "MEMORY.md":
                    conn.execute(
                        "DELETE FROM memories WHERE source = 'memory_md' AND date = 'longterm'"
                    )
                elif rel.startswith("memory/summaries/"):
                    # memory/summaries/2026-02-19_1400.md → date = 2026-02-19_1400
                    date_str = Path(rel).stem
                    conn.execute(
                        "DELETE FROM memories WHERE source = 'summary' AND date = ?",
                        (date_str,),
                    )
                else:
                    # memory/2026-02-15.md → date = 2026-02-15
                    date_str = Path(rel).stem
                    conn.execute(
                        "DELETE FROM memories WHERE source = 'daily' AND date = ?",
                        (date_str,),
                    )
                cleaned += 1
        return cleaned

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(self, query: str, days: int | None = None) -> list[dict]:
        """Search memories using LIKE (Chinese-friendly).

        Args:
            query: Search string (case-insensitive via LIKE).
            days: Optional — limit to daily memories from the last N days.

        Returns:
            List of dicts with keys: source, date, line_num, content.
        """
        self.sync()
        conn = self.connect()

        sql = (
            "SELECT source, date, line_num, content FROM memories WHERE content LIKE ?"
        )
        params: list[str | int] = [f"%{query}%"]

        if days is not None:
            sql += " AND source = 'daily' ORDER BY date DESC, line_num ASC"
        else:
            sql += " ORDER BY date DESC, line_num ASC"

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

    def get_stats(self) -> dict:
        """Return memory statistics."""
        self.sync()
        conn = self.connect()

        total_rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        daily_count = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM memories WHERE source = 'daily'"
        ).fetchone()[0]
        has_longterm = (
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE source = 'memory_md'"
            ).fetchone()[0]
            > 0
        )

        return {
            "total_lines": total_rows,
            "daily_count": daily_count,
            "has_longterm": has_longterm,
        }
