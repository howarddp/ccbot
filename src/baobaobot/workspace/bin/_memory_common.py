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
import logging
import os
import re
import shutil
import sqlite3
import struct
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedding configuration
# ---------------------------------------------------------------------------
_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIMS = 512
_EMBEDDING_BATCH_SIZE = 100  # max paragraphs per API call
_EMBEDDING_SYNC_TIMEOUT = 5.0  # seconds — stop embedding after this during sync

# Regex to strip YAML frontmatter (--- ... ---) from the beginning of a file
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)

# Extract tags: [tag1, tag2] from frontmatter
_TAGS_BRACKET_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)

# Inline tags: #word (supports mixed case, normalized to lowercase)
_INLINE_TAG_RE = re.compile(r"(?:^|(?<=\s))#([a-zA-Z][a-zA-Z0-9/-]*)")

# Attachment references: ![desc](path) for images, [desc](path) for files
_ATTACHMENT_RE = re.compile(r"!?\[([^\]]+)\]\(([^)]+)\)")

# Regex to insert spaces at CJK ↔ ASCII boundaries for FTS5 tokenization
_CJK_RANGE = (
    r"\u2e80-\u9fff\uf900-\ufaff"  # CJK Unified + Compatibility
    r"\U00020000-\U0002a6df"        # CJK Extension B
)
_CJK_TO_ASCII = re.compile(rf"([{_CJK_RANGE}])([A-Za-z0-9])")
_ASCII_TO_CJK = re.compile(rf"([A-Za-z0-9])([{_CJK_RANGE}])")


def _pad_cjk_ascii(text: str) -> str:
    """Insert spaces at CJK ↔ ASCII boundaries for FTS5 tokenization."""
    text = _CJK_TO_ASCII.sub(r"\1 \2", text)
    text = _ASCII_TO_CJK.sub(r"\1 \2", text)
    return text


# Schema version — MUST match baobaobot.memory.db._SCHEMA_VERSION
_SCHEMA_VERSION = 6

# Heading regex for paragraph splitting
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)

# Max paragraph length (characters) before forced split
_PARA_MAX_CHARS = 500

# Min paragraph length — shorter paragraphs are merged with the next one
_PARA_MIN_CHARS = 25


class _Paragraph:
    """A paragraph extracted from a memory file."""

    __slots__ = ("heading", "content", "line_start", "line_end")

    def __init__(self, heading: str, content: str, line_start: int, line_end: int):
        self.heading = heading
        self.content = content
        self.line_start = line_start
        self.line_end = line_end


def _normalize_for_hash(text: str) -> str:
    """Normalize paragraph content for consistent hashing.

    Strips whitespace per line and removes blank lines so that minor
    formatting changes (trailing spaces, extra blank lines) don't
    invalidate the embedding cache.
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _split_paragraphs(content: str) -> list[_Paragraph]:
    """Split memory file content (frontmatter already stripped) into paragraphs.

    Rules:
    1. ``##`` (or any level heading) starts a new paragraph; heading text is
       included in the paragraph content.
    2. Sections without headings are split on blank lines.
    3. Paragraphs exceeding ``_PARA_MAX_CHARS`` are force-split.
    4. Paragraphs shorter than ``_PARA_MIN_CHARS`` are merged with the next.
    5. Pure separator lines (``---``), empty headings, and whitespace-only
       paragraphs are discarded.
    """
    lines = content.splitlines()
    if not lines:
        return []

    # --- Phase 1: group lines into raw paragraphs -------------------------
    raw_groups: list[tuple[str, list[str], int]] = []  # (heading, lines, start_line)
    current_heading = ""
    current_lines: list[str] = []
    current_start = 1

    for idx, line in enumerate(lines, 1):
        stripped = line.strip()

        # Heading starts a new paragraph
        if _HEADING_RE.match(line):
            # Flush previous group
            if current_lines:
                raw_groups.append((current_heading, current_lines, current_start))
            current_heading = stripped
            current_lines = [stripped]
            current_start = idx
            continue

        # Blank line — split if we have accumulated content and no heading
        if not stripped:
            if current_lines and not current_heading:
                raw_groups.append((current_heading, current_lines, current_start))
                current_heading = ""
                current_lines = []
                current_start = idx + 1
            # Within a headed section, blank lines don't split
            continue

        # Skip separator lines
        if stripped == "---":
            continue

        if not current_lines:
            current_start = idx
        current_lines.append(stripped)

    # Flush last group
    if current_lines:
        raw_groups.append((current_heading, current_lines, current_start))

    # --- Phase 2: force-split long paragraphs ------------------------------
    split_groups: list[tuple[str, str, int, int]] = []  # (heading, content, start, end)
    for heading, grp_lines, start in raw_groups:
        text = "\n".join(grp_lines)
        if len(text) <= _PARA_MAX_CHARS:
            split_groups.append((heading, text, start, start + len(grp_lines) - 1))
        else:
            # Split by lines, accumulating until max
            chunk_lines: list[str] = []
            chunk_start = start
            chunk_len = 0
            for i, ln in enumerate(grp_lines):
                if chunk_len + len(ln) + 1 > _PARA_MAX_CHARS and chunk_lines:
                    split_groups.append((
                        heading,
                        "\n".join(chunk_lines),
                        chunk_start,
                        chunk_start + len(chunk_lines) - 1,
                    ))
                    heading = ""  # subsequent chunks lose the heading
                    chunk_lines = []
                    chunk_start = start + i
                    chunk_len = 0
                chunk_lines.append(ln)
                chunk_len += len(ln) + 1
            if chunk_lines:
                split_groups.append((
                    heading,
                    "\n".join(chunk_lines),
                    chunk_start,
                    chunk_start + len(chunk_lines) - 1,
                ))

    # --- Phase 3: merge short paragraphs -----------------------------------
    merged: list[_Paragraph] = []
    for heading, text, start, end in split_groups:
        # Filter out whitespace-only or too-short content
        if len(text.strip()) < _PARA_MIN_CHARS:
            # Merge into previous paragraph if one exists
            if merged:
                prev = merged[-1]
                prev.content += "\n" + text
                prev.line_end = end
                if not prev.heading and heading:
                    prev.heading = heading
            continue
        merged.append(_Paragraph(heading, text, start, end))

    return merged


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


# ---------------------------------------------------------------------------
# Dedup helpers — character-bigram Jaccard similarity
# IMPORTANT: keep in sync with baobaobot.memory.db
# ---------------------------------------------------------------------------

_SOURCE_PRIORITY: dict[str, int] = {"experience": 0, "daily": 1, "todo": 2, "cron": 3, "summary": 4}

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

CREATE TABLE IF NOT EXISTS paragraphs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    date         TEXT    NOT NULL,
    heading      TEXT    NOT NULL DEFAULT '',
    content      TEXT    NOT NULL,
    line_start   INTEGER NOT NULL,
    line_end     INTEGER NOT NULL,
    content_hash TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT    NOT NULL,
    model_name   TEXT    NOT NULL,
    embedding    BLOB    NOT NULL,
    token_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (content_hash, model_name)
);

CREATE INDEX IF NOT EXISTS idx_memories_date    ON memories(date);
CREATE INDEX IF NOT EXISTS idx_memories_source  ON memories(source);
CREATE INDEX IF NOT EXISTS idx_memories_path    ON memories(path);
CREATE INDEX IF NOT EXISTS idx_attachment_path  ON attachment_meta(memory_path);
CREATE INDEX IF NOT EXISTS idx_paragraphs_path  ON paragraphs(path);
CREATE INDEX IF NOT EXISTS idx_paragraphs_hash  ON paragraphs(content_hash);
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
        # Only DROP tables that can be rebuilt from markdown files / API.
        # ⚠️ NEVER DROP: todos (sole data source is DB, no markdown backup)
        conn.executescript(
            "DROP TABLE IF EXISTS memories_fts;\n"
            "DROP TABLE IF EXISTS attachment_meta;\n"
            "DROP TABLE IF EXISTS memories;\n"
            "DROP TABLE IF EXISTS file_meta;\n"
            "DROP TABLE IF EXISTS paragraphs;\n"
            "DROP TABLE IF EXISTS embedding_cache;\n"
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

    # Check embedding capabilities (sqlite-vec + API key)
    _check_embedding_capabilities(conn)

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
            if "attachments/" not in fpath:
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
    """Index a single .md file into the memories and paragraphs tables."""
    current_hash = _file_hash(path)
    now = datetime.now().isoformat()

    conn.execute("DELETE FROM memories WHERE path = ?", (rel,))
    conn.execute("DELETE FROM paragraphs WHERE path = ?", (rel,))
    conn.execute("DELETE FROM attachment_meta WHERE memory_path = ?", (rel,))

    try:
        raw_content = path.read_text(encoding="utf-8")
    except OSError:
        return

    # Parse tags from raw content (before stripping frontmatter)
    tags = _parse_tags(raw_content)

    # Strip frontmatter for indexing
    content = _strip_frontmatter(raw_content)

    # --- Line-level indexing (for FTS5) ---
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped:
            conn.execute(
                "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rel, source, date_str, i, _pad_cjk_ascii(stripped), now),
            )

    # --- Paragraph-level indexing (for vector search) ---
    for para in _split_paragraphs(content):
        content_hash = hashlib.md5(
            _normalize_for_hash(para.content).encode("utf-8")
        ).hexdigest()
        conn.execute(
            "INSERT INTO paragraphs "
            "(path, source, date, heading, content, line_start, line_end, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rel, source, date_str, para.heading, para.content, para.line_start, para.line_end, content_hash),
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


# ---------------------------------------------------------------------------
# Embedding: OpenAI API + sqlite-vec
# ---------------------------------------------------------------------------

# Module-level capability flag (set once per process in connect_db via
# _check_embedding_capabilities).  Only requires openai + API key;
# cosine similarity is computed in pure Python (no sqlite-vec needed).
_embedding_enabled = False


def _load_dotenv_once() -> None:
    """Load .env from baobaobot config dir if OPENAI_API_KEY is not already set."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    # Resolve config dir: BAOBAOBOT_DIR env → pointer file → ~/.baobaobot
    config_dir = os.environ.get("BAOBAOBOT_DIR", "")
    if not config_dir:
        pointer = Path.home() / ".config" / "baobaobot" / "dir"
        if pointer.is_file():
            config_dir = pointer.read_text().strip()
    if not config_dir:
        config_dir = str(Path.home() / ".baobaobot")
    env_file = Path(config_dir) / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key and key not in os.environ:
                    os.environ[key] = val


def _check_embedding_capabilities(conn: sqlite3.Connection) -> None:
    """Detect whether vector search is available (openai + API key).

    Note: We compute cosine similarity in pure Python, so sqlite-vec
    extension loading is NOT required.  Only the ``openai`` package
    and a valid API key are needed.
    """
    global _embedding_enabled

    # Ensure API key is loaded from .env if not in environment
    _load_dotenv_once()

    has_openai = False
    try:
        import openai as _oa  # noqa: F401

        has_openai = True
    except ImportError:
        pass

    _embedding_enabled = has_openai and bool(os.environ.get("OPENAI_API_KEY"))

    status = "enabled" if _embedding_enabled else "disabled"
    reason = ""
    if not _embedding_enabled:
        reasons = []
        if not has_openai:
            reasons.append("openai package not installed")
        if not os.environ.get("OPENAI_API_KEY"):
            reasons.append("OPENAI_API_KEY not set")
        reason = f" ({', '.join(reasons)})"
    logger.info("Vector search: %s%s", status, reason)


def _get_openai_embeddings(texts: list[str]) -> tuple[list[list[float]], int]:
    """Call OpenAI embedding API. Returns (vectors, total_tokens)."""
    from openai import OpenAI

    client = OpenAI()
    response = client.embeddings.create(
        model=_EMBEDDING_MODEL,
        input=texts,
        dimensions=_EMBEDDING_DIMS,
    )
    # Sort by index to maintain input order
    sorted_data = sorted(response.data, key=lambda x: x.index)
    total_tokens = response.usage.total_tokens if response.usage else len(texts)
    return [d.embedding for d in sorted_data], total_tokens


def _serialize_embedding(vector: list[float]) -> bytes:
    """Serialize embedding vector to bytes for SQLite BLOB storage."""
    return struct.pack(f"<{_EMBEDDING_DIMS}f", *vector)


def _deserialize_embedding(blob: bytes) -> list[float]:
    """Deserialize embedding vector from SQLite BLOB."""
    return list(struct.unpack(f"<{_EMBEDDING_DIMS}f", blob))


def _compute_embeddings(conn: sqlite3.Connection, timeout: float = _EMBEDDING_SYNC_TIMEOUT) -> int:
    """Compute embeddings for paragraphs that don't have one yet.

    Uses LEFT JOIN to find paragraphs missing from embedding_cache,
    independent of file_meta state. Returns number of new embeddings computed.
    """
    if not _embedding_enabled:
        return 0

    import time

    start_time = time.monotonic()

    # Find paragraphs missing embeddings (deduplicated by content_hash)
    rows = conn.execute(
        "SELECT DISTINCT p.content_hash, p.content "
        "FROM paragraphs p "
        "LEFT JOIN embedding_cache e "
        "  ON p.content_hash = e.content_hash "
        "  AND e.model_name = ? "
        "WHERE e.content_hash IS NULL",
        (_EMBEDDING_MODEL,),
    ).fetchall()

    if not rows:
        return 0

    total_computed = 0
    now = datetime.now().isoformat()

    # Process in batches
    for batch_start in range(0, len(rows), _EMBEDDING_BATCH_SIZE):
        # Check timeout
        if time.monotonic() - start_time > timeout:
            logger.info(
                "Embedding timeout after %d/%d paragraphs, rest will be computed next sync",
                total_computed,
                len(rows),
            )
            break

        batch = rows[batch_start : batch_start + _EMBEDDING_BATCH_SIZE]
        texts = [r["content"] for r in batch]
        hashes = [r["content_hash"] for r in batch]

        try:
            vectors, batch_tokens = _get_openai_embeddings(texts)
        except Exception as exc:
            logger.warning("Embedding API error: %s — skipping batch, will retry next sync", exc)
            continue

        # Distribute total tokens evenly across batch items
        per_item_tokens = max(1, batch_tokens // len(texts))

        # Store in embedding_cache
        for content_hash, vector in zip(hashes, vectors):
            blob = _serialize_embedding(vector)
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache "
                "(content_hash, model_name, embedding, token_count, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (content_hash, _EMBEDDING_MODEL, blob, per_item_tokens, now),
            )
            total_computed += 1

        conn.commit()

    if total_computed:
        logger.info("Computed %d new embeddings (%d remaining)", total_computed, len(rows) - total_computed)

    return total_computed


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


def _sync_todos(conn: sqlite3.Connection) -> int:
    """Sync TODO items into the memories table as source='todo'."""
    virtual_path = "__todos__"
    now = datetime.now().isoformat()

    # Check if todos table exists
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='todos'"
    ).fetchone()
    if not has_table:
        return 0

    # Serialize current TODOs for change detection
    rows = conn.execute(
        "SELECT id, type, title, content, created_by, status, deadline "
        "FROM todos ORDER BY id"
    ).fetchall()
    serialized = json.dumps(
        [dict(r) for r in rows], ensure_ascii=False, sort_keys=True
    )
    current_hash = hashlib.md5(serialized.encode()).hexdigest()

    # Check if anything changed
    meta = conn.execute(
        "SELECT content_hash FROM file_meta WHERE path = ?", (virtual_path,)
    ).fetchone()
    if meta and meta["content_hash"] == current_hash:
        return 0

    # Clear old todo entries and re-index
    conn.execute("DELETE FROM memories WHERE path = ?", (virtual_path,))
    conn.execute("DELETE FROM paragraphs WHERE path = ?", (virtual_path,))

    line_num = 0
    for r in rows:
        line_num += 1
        status_mark = "done" if r["status"] == "done" else "open"
        title_line = f"[{r['id']}] [{r['type']}] {r['title']} ({status_mark})"
        if r["created_by"]:
            title_line += f" @{r['created_by']}"
        conn.execute(
            "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
            "VALUES (?, 'todo', ?, ?, ?, ?)",
            (virtual_path, r["id"], line_num, _pad_cjk_ascii(title_line), now),
        )
        # Index content lines if present
        if r["content"]:
            for content_line in r["content"].splitlines():
                stripped = content_line.strip()
                if stripped:
                    line_num += 1
                    conn.execute(
                        "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
                        "VALUES (?, 'todo', ?, ?, ?, ?)",
                        (virtual_path, r["id"], line_num, _pad_cjk_ascii(stripped), now),
                    )

        # Write to paragraphs table for vector search
        para_text = title_line
        if r["content"]:
            para_text += f"\n{r['content']}"
        content_hash = hashlib.md5(
            _normalize_for_hash(para_text).encode()
        ).hexdigest()
        conn.execute(
            "INSERT INTO paragraphs "
            "(path, source, date, heading, content, line_start, line_end, content_hash) "
            "VALUES (?, 'todo', ?, '', ?, 0, 0, ?)",
            (virtual_path, r["id"], para_text, content_hash),
        )

    conn.execute(
        "INSERT OR REPLACE INTO file_meta (path, content_hash, synced_at, tags) "
        "VALUES (?, ?, ?, '[]')",
        (virtual_path, current_hash, now),
    )
    return 1


def _sync_cron(conn: sqlite3.Connection, workspace: Path) -> int:
    """Sync cron jobs into the memories table as source='cron'.

    Reads from the cron_jobs table in memory.db (same connection).
    """
    virtual_path = "__cron__"
    now = datetime.now().isoformat()

    # Check if cron_jobs table exists in this DB
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cron_jobs'"
    ).fetchone()
    if not has_table:
        # No cron_jobs table — clean up stale index if any
        meta = conn.execute(
            "SELECT path FROM file_meta WHERE path = ?", (virtual_path,)
        ).fetchone()
        if meta:
            conn.execute("DELETE FROM memories WHERE path = ?", (virtual_path,))
            conn.execute("DELETE FROM paragraphs WHERE path = ?", (virtual_path,))
            conn.execute("DELETE FROM file_meta WHERE path = ?", (virtual_path,))
            return 1
        return 0

    # Read jobs from cron_jobs table
    rows = conn.execute(
        "SELECT id, name, message, enabled FROM cron_jobs ORDER BY created_at"
    ).fetchall()

    # Change detection: hash of all job fields
    combined = "|".join(
        f"{r['id']}:{r['name']}:{r['message']}:{r['enabled']}" for r in rows
    )
    current_hash = hashlib.md5(combined.encode()).hexdigest()
    meta = conn.execute(
        "SELECT content_hash FROM file_meta WHERE path = ?", (virtual_path,)
    ).fetchone()
    if meta and meta["content_hash"] == current_hash:
        return 0

    # Clear old cron entries and re-index
    conn.execute("DELETE FROM memories WHERE path = ?", (virtual_path,))
    conn.execute("DELETE FROM paragraphs WHERE path = ?", (virtual_path,))

    line_num = 0
    for r in rows:
        job_id = r["id"]
        name = r["name"]
        message = r["message"]
        enabled = r["enabled"]
        status = "enabled" if enabled else "paused"

        line_num += 1
        title_line = f"[{job_id}] {name} ({status})"
        conn.execute(
            "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
            "VALUES (?, 'cron', ?, ?, ?, ?)",
            (virtual_path, job_id, line_num, _pad_cjk_ascii(title_line), now),
        )
        if message:
            line_num += 1
            conn.execute(
                "INSERT INTO memories (path, source, date, line_num, content, updated_at) "
                "VALUES (?, 'cron', ?, ?, ?, ?)",
                (virtual_path, job_id, line_num, _pad_cjk_ascii(message), now),
            )

        # Write to paragraphs table for vector search
        para_text = title_line
        if message:
            para_text += f"\n{message}"
        content_hash_p = hashlib.md5(
            _normalize_for_hash(para_text).encode()
        ).hexdigest()
        conn.execute(
            "INSERT INTO paragraphs "
            "(path, source, date, heading, content, line_start, line_end, content_hash) "
            "VALUES (?, 'cron', ?, '', ?, 0, 0, ?)",
            (virtual_path, job_id, para_text, content_hash_p),
        )

    conn.execute(
        "INSERT OR REPLACE INTO file_meta (path, content_hash, synced_at, tags) "
        "VALUES (?, ?, ?, '[]')",
        (virtual_path, current_hash, now),
    )
    return 1


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

    # Sync TODOs into memories table
    synced += _sync_todos(conn)

    # Sync Cron jobs into memories table
    synced += _sync_cron(conn, workspace)

    # Clean up deleted files (skip virtual paths used by todo/cron sync)
    rows = conn.execute("SELECT path FROM file_meta").fetchall()
    for row in rows:
        rel = row["path"]
        if rel.startswith("__"):
            continue  # virtual paths for todo/cron
        full = workspace / rel
        if not full.exists():
            conn.execute("DELETE FROM file_meta WHERE path = ?", (rel,))
            conn.execute("DELETE FROM memories WHERE path = ?", (rel,))
            conn.execute("DELETE FROM paragraphs WHERE path = ?", (rel,))
            conn.execute("DELETE FROM attachment_meta WHERE memory_path = ?", (rel,))
            synced += 1

    conn.commit()

    # Rebuild FTS index if anything changed
    if synced:
        _rebuild_fts(conn)

    # Compute embeddings for paragraphs that don't have one yet.
    # Runs even when synced==0 (catches paragraphs from previous failed attempts).
    _compute_embeddings(conn)

    return synced


# Source weights for hybrid search ranking
_SOURCE_WEIGHT: dict[str, float] = {
    "experience": 1.3,
    "daily": 1.0,
    "summary": 0.8,
}

_SEARCH_LIMIT = 200  # max results from FTS/LIKE to prevent huge result sets


def search(
    conn: sqlite3.Connection,
    query: str,
    days: int | None = None,
    tag: str | None = None,
    mode: str = "hybrid",
) -> tuple[list[dict], str]:
    """Search memories.

    Args:
        conn: SQLite connection (already synced).
        query: Search string.
        days: Optional — limit to daily memories from the last N days.
        tag: Optional — filter by tag name (without # prefix).
        mode: "keyword" (FTS5 only), "vector" (embedding only),
              or "hybrid" (both + RRF merge). Default "hybrid".

    Returns:
        Tuple of (results, effective_mode).
        results: List of dicts with keys: source, date, line_num, content.
            In hybrid/vector mode, also includes: heading, paragraph.
        effective_mode: The mode actually used (may differ from requested
            if embedding is unavailable).
    """
    # Determine effective mode
    effective_mode = mode
    if mode in ("hybrid", "vector") and not _embedding_enabled:
        effective_mode = "keyword"

    if effective_mode == "keyword":
        return _search_keyword(conn, query, days, tag), effective_mode

    if effective_mode == "vector":
        return _search_vector_only(conn, query, days, tag), effective_mode

    # hybrid mode: FTS/LIKE + vector, merged via RRF
    return _search_hybrid(conn, query, days, tag), effective_mode


def _search_keyword(
    conn: sqlite3.Connection,
    query: str,
    days: int | None,
    tag: str | None,
) -> list[dict]:
    """Original keyword search (FTS5 with LIKE fallback)."""
    use_fts = _fts_available and query.isascii()
    if use_fts:
        try:
            rows = _search_fts(conn, query, days, tag)
            return _dedup_results([dict(r) for r in rows])
        except sqlite3.OperationalError:
            pass

    rows = _search_like(conn, query, days, tag)
    return _dedup_results([dict(r) for r in rows])


def _search_vector(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 50,
    days: int | None = None,
    tag: str | None = None,
) -> list[dict]:
    """Search paragraphs by embedding cosine similarity.

    Returns list of dicts with: source, date, line_num (=line_start),
    content (=paragraph text), heading, content_hash, similarity.
    """
    if not _embedding_enabled:
        return []

    try:
        vecs, _ = _get_openai_embeddings([query])
        query_vec = vecs[0]
    except Exception as exc:
        logger.warning("Embedding query failed: %s", exc)
        return []

    query_blob = _serialize_embedding(query_vec)

    # Compute cosine similarity in Python (avoids vec0 virtual table complexity)
    # Fetch all cached embeddings and compute dot product
    rows = conn.execute(
        "SELECT DISTINCT p.id, p.source, p.date, p.heading, p.content, "
        "p.line_start, p.line_end, p.content_hash, e.embedding "
        "FROM paragraphs p "
        "JOIN embedding_cache e ON p.content_hash = e.content_hash "
        "  AND e.model_name = ?",
        (_EMBEDDING_MODEL,),
    ).fetchall()

    if not rows:
        return []

    # Compute cosine similarities
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        stored_vec = _deserialize_embedding(row["embedding"])
        sim = _cosine_similarity(query_vec, stored_vec)
        scored.append((sim, row))

    # Sort by similarity descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Apply similarity threshold and filters, take top-K
    _SIMILARITY_THRESHOLD = 0.18  # lowered for better CJK semantic recall
    results: list[dict] = []
    cutoff_date = None
    if days is not None:
        cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    for sim, row in scored[:top_k * 2]:  # over-fetch to account for filtering
        if len(results) >= top_k:
            break

        # Skip results below similarity threshold
        if sim < _SIMILARITY_THRESHOLD:
            break  # sorted desc, all remaining will be lower

        source = row["source"]

        # Days filter: only apply to daily/summary
        if cutoff_date and source in ("daily", "summary") and row["date"] < cutoff_date:
            continue

        # Tag filter
        if tag is not None:
            tag_row = conn.execute(
                "SELECT tags FROM file_meta WHERE path = (SELECT path FROM paragraphs WHERE id = ?)",
                (row["id"],),
            ).fetchone()
            if not tag_row or f'"{tag}"' not in tag_row["tags"]:
                continue

        results.append({
            "source": source,
            "date": row["date"],
            "line_num": row["line_start"],
            "content": row["content"],
            "heading": row["heading"],
            "similarity": sim,
            "_para_id": row["id"],
        })

    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _line_to_paragraph(conn: sqlite3.Connection, row: dict) -> int | None:
    """Map a line-level result to its paragraph ID."""
    # Use the path from the memories row to find the paragraph
    mem_row = conn.execute(
        "SELECT path FROM memories WHERE source = ? AND date = ? AND line_num = ? LIMIT 1",
        (row["source"], row["date"], row["line_num"]),
    ).fetchone()
    if not mem_row:
        return None

    para = conn.execute(
        "SELECT id FROM paragraphs "
        "WHERE path = ? AND line_start <= ? AND line_end >= ? LIMIT 1",
        (mem_row["path"], row["line_num"], row["line_num"]),
    ).fetchone()
    return para["id"] if para else None


def _rrf_merge(
    fts_para_ids: list[int],
    vec_para_ids: list[int],
    k: int = 60,
) -> dict[int, float]:
    """Reciprocal Rank Fusion on paragraph IDs.

    Returns dict of para_id → RRF score.
    """
    scores: dict[int, float] = {}
    for rank, pid in enumerate(fts_para_ids):
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank)
    for rank, pid in enumerate(vec_para_ids):
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank)
    return scores


def _search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    days: int | None,
    tag: str | None,
) -> list[dict]:
    """Hybrid search: FTS/LIKE + vector, merged via RRF."""
    # 1. Keyword search (line-level)
    kw_results = _search_keyword(conn, query, days, tag)

    # 2. Vector search (paragraph-level)
    vec_results = _search_vector(conn, query, top_k=50, days=days, tag=tag)

    # If only one side has results, return that
    if not vec_results:
        return kw_results
    if not kw_results:
        return vec_results

    # 3. Map keyword results to paragraph IDs
    fts_para_ids: list[int] = []
    fts_seen: set[int] = set()
    for r in kw_results:
        pid = _line_to_paragraph(conn, r)
        if pid and pid not in fts_seen:
            fts_para_ids.append(pid)
            fts_seen.add(pid)

    vec_para_ids = [r["_para_id"] for r in vec_results]

    # 4. RRF merge
    rrf_scores = _rrf_merge(fts_para_ids, vec_para_ids)

    # Apply source weights
    # Build para_id → source mapping
    all_para_ids = list(rrf_scores.keys())
    if all_para_ids:
        placeholders = ",".join("?" * len(all_para_ids))
        para_rows = conn.execute(
            f"SELECT id, source, date, heading, content, line_start FROM paragraphs "
            f"WHERE id IN ({placeholders})",
            all_para_ids,
        ).fetchall()
        para_map = {r["id"]: dict(r) for r in para_rows}
    else:
        para_map = {}

    for pid, score in rrf_scores.items():
        if pid in para_map:
            source = para_map[pid]["source"]
            rrf_scores[pid] = score * _SOURCE_WEIGHT.get(source, 1.0)

    # 5. Sort by RRF score
    sorted_ids = sorted(rrf_scores, key=lambda pid: rrf_scores[pid], reverse=True)

    # 6. Build result list (paragraph-level)
    results: list[dict] = []
    for pid in sorted_ids:
        if pid not in para_map:
            continue
        p = para_map[pid]
        results.append({
            "source": p["source"],
            "date": p["date"],
            "line_num": p["line_start"],
            "content": p["content"],
            "heading": p["heading"],
        })

    # 7. Append TODO/Cron results from keyword search (they don't have paragraphs)
    for r in kw_results:
        if r["source"] in ("todo", "cron"):
            results.append(r)

    return results


def _search_vector_only(
    conn: sqlite3.Connection,
    query: str,
    days: int | None,
    tag: str | None,
) -> list[dict]:
    """Pure vector search."""
    return _search_vector(conn, query, top_k=50, days=days, tag=tag)


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
        conditions.append("m.source IN ('daily', 'summary')")
        conditions.append("m.date >= ?")
        params.append(cutoff)

    sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY fts.rank LIMIT {_SEARCH_LIMIT}"

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
        conditions.append("m.source IN ('daily', 'summary')")
        conditions.append("m.date >= ?")
        params.append(cutoff)

    sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY m.date DESC, m.line_num ASC LIMIT {_SEARCH_LIMIT}"

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

EXPERIENCE_FRONTMATTER_TEMPLATE = """\
---
topic: "{topic}"
tags: []
created: {date}
updated: {date}
---
"""

# Regex to match `updated: YYYY-MM-DD` in YAML frontmatter
_UPDATED_RE = re.compile(r"^(updated:\s*)\d{4}-\d{2}-\d{2}", re.MULTILINE)


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

    rel_path = f"attachments/{date_str}/{dest_name}"
    return rel_path, dest_name


def attachment_ref(source: Path, description: str, rel_path: str) -> str:
    """Build Markdown reference for attachment (image vs file link)."""
    suffix = source.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return f"![{description}]({rel_path})"
    return f"[{description}]({rel_path})"


def _experience_heading(topic: str) -> str:
    """Generate a heading from a topic name.

    If the topic looks like kebab-case ASCII (e.g. 'user-preferences'),
    convert to title case ('User Preferences'). Otherwise use as-is
    (e.g. Chinese '使用者偏好' stays unchanged).
    """
    if re.fullmatch(r"[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*", topic):
        return topic.replace("-", " ").title()
    return topic


def append_to_experience_file(workspace: Path, topic: str, line: str) -> str:
    """Append line to experience topic file, creating if needed.

    New files get YAML frontmatter (topic, tags, created, updated) and a heading.
    Existing files get the ``updated`` field bumped to today.

    Returns relative path of the experience file.
    """
    exp_dir = workspace / "memory" / "experience"
    exp_dir.mkdir(parents=True, exist_ok=True)
    path = exp_dir / f"{topic}.md"
    today = date.today().isoformat()

    if not path.exists():
        heading = _experience_heading(topic)
        frontmatter = EXPERIENCE_FRONTMATTER_TEMPLATE.format(topic=topic, date=today)
        path.write_text(f"{frontmatter}# {heading}\n\n{line}\n", encoding="utf-8")
    else:
        content = path.read_text(encoding="utf-8")
        content = _UPDATED_RE.sub(rf"\g<1>{today}", content, count=1)
        if not content.endswith("\n"):
            content += "\n"
        path.write_text(content + line + "\n", encoding="utf-8")

    return f"memory/experience/{topic}.md"


def format_file_label(row: dict | sqlite3.Row) -> str:
    """Convert a result row's source/date into a human-readable file label."""
    source = row["source"]
    if source == "experience":
        return f"memory/experience/{row['date']}.md"
    elif source == "summary":
        return f"memory/summaries/{row['date']}.md"
    elif source == "todo":
        return f"\u2705 TODO {row['date']}"
    elif source == "cron":
        return f"\u23f0 Cron {row['date']}"
    else:
        # Daily: date is 'YYYY-MM-DD', path is 'memory/daily/YYYY-MM/YYYY-MM-DD.md'
        d = row["date"]
        return f"memory/daily/{d[:7]}/{d}.md"


# ---------------------------------------------------------------------------
# Git integration (duplicated from baobaobot.memory.git for standalone use)
# NOTE: Keep in sync with baobaobot.memory.git
# ---------------------------------------------------------------------------

_GITIGNORE_CONTENT = """\
memory.db
memory.db-journal
memory.db-wal
memory.db-shm
__pycache__/
"""


def ensure_git_repo(memory_dir: Path) -> bool:
    """Initialize a git repo in memory_dir if one doesn't exist."""
    if not memory_dir.is_dir():
        return False

    git_dir = memory_dir / ".git"
    if git_dir.exists():
        return True

    try:
        subprocess.run(["git", "init"], cwd=memory_dir, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "config", "user.name", "baobaobot"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.email", "noreply@baobaobot"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        gitignore = memory_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        subprocess.run(
            ["git", "add", ".gitignore"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", "init: memory git tracking"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        logger.info("Initialized git repo in %s", memory_dir)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to initialize git repo in %s", memory_dir)
        return False


def commit_memory(memory_dir: Path, message: str) -> bool:
    """Stage all changes and commit in the memory directory."""
    if not memory_dir.is_dir():
        return False

    if not ensure_git_repo(memory_dir):
        return False

    try:
        subprocess.run(
            ["git", "add", "."],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return False

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        logger.debug("Memory commit: %s", message)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to commit memory: %s", message)
        return False
