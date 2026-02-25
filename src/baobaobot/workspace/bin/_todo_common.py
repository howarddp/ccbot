"""Shared TODO database utilities for standalone bin scripts.

Provides SQLite-backed TODO management: create, list, update, delete.
Stores todos in the existing memory.db (adds a `todos` table).

IMPORTANT: This module must be self-contained (no imports from baobaobot.*)
since bin scripts run outside the package.

Used by: todo-add, todo-list, todo-get, todo-done, todo-update, todo-remove, todo-export
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

_TODO_SCHEMA = """\
CREATE TABLE IF NOT EXISTS todos (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL DEFAULT 'task',
    title         TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_by_id TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    deadline      TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    done_at       TEXT,
    attachments   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_type ON todos(type);
CREATE INDEX IF NOT EXISTS idx_todos_deadline ON todos(deadline);
CREATE INDEX IF NOT EXISTS idx_todos_created_by ON todos(created_by);
"""


def connect_db(workspace: Path) -> sqlite3.Connection:
    """Open memory.db and ensure the todos table exists."""
    db_path = workspace / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Check if todos table exists; create if not
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='todos'"
    ).fetchone()
    if row is None:
        conn.executescript(_TODO_SCHEMA)
        conn.commit()
    return conn


def resolve_workspace(args_workspace: str | None = None) -> Path:
    """Resolve workspace directory: explicit arg > cwd (if has memory/) > error."""
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


def generate_todo_id(conn: sqlite3.Connection, date_str: str | None = None) -> str:
    """Generate a new todo ID: T{YYYYMMDD}-{N}."""
    if date_str is None:
        date_str = date.today().strftime("%Y%m%d")
    else:
        date_str = date_str.replace("-", "")
    prefix = f"T{date_str}-"
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, ?) AS INTEGER)) AS max_n "
        "FROM todos WHERE id LIKE ?",
        (len(prefix) + 1, f"{prefix}%"),
    ).fetchone()
    n = (row["max_n"] if row and row["max_n"] else 0) + 1
    return f"{prefix}{n}"


def add_todo(
    conn: sqlite3.Connection,
    title: str,
    *,
    todo_type: str = "task",
    user: str = "",
    user_id: str = "",
    deadline: str | None = None,
    content: str = "",
    attachments: list[str] | None = None,
) -> str:
    """Insert a new todo. Returns the generated ID."""
    now = datetime.now().isoformat(timespec="seconds")
    todo_id = generate_todo_id(conn)
    conn.execute(
        "INSERT INTO todos (id, type, title, content, created_by, created_by_id, "
        "created_at, deadline, status, attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (
            todo_id,
            todo_type,
            title,
            content,
            user,
            user_id,
            now,
            deadline,
            json.dumps(attachments or []),
        ),
    )
    conn.commit()
    return todo_id


def list_todos(
    conn: sqlite3.Connection,
    *,
    status: str = "open",
    todo_type: str | None = None,
    user: str | None = None,
    before: str | None = None,
    after: str | None = None,
    overdue: bool = False,
) -> list[sqlite3.Row]:
    """List todos with optional filters."""
    conditions: list[str] = []
    params: list[str] = []

    if status != "all":
        conditions.append("status = ?")
        params.append(status)
    if todo_type:
        conditions.append("type = ?")
        params.append(todo_type)
    if user:
        conditions.append("created_by = ?")
        params.append(user)
    if before:
        conditions.append("deadline IS NOT NULL AND deadline <= ?")
        params.append(before)
    if after:
        conditions.append("created_at >= ?")
        params.append(after)
    if overdue:
        today = date.today().isoformat()
        conditions.append("deadline IS NOT NULL AND deadline < ? AND status = 'open'")
        params.append(today)

    sql = "SELECT * FROM todos"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY CASE WHEN deadline IS NOT NULL THEN 0 ELSE 1 END, deadline ASC, created_at DESC"

    return conn.execute(sql, params).fetchall()


def get_todo(conn: sqlite3.Connection, todo_id: str) -> sqlite3.Row | None:
    """Get a single todo by ID."""
    return conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()


def done_todo(conn: sqlite3.Connection, todo_id: str) -> bool:
    """Mark a todo as done. Returns True if updated."""
    row = get_todo(conn, todo_id)
    if row is None:
        return False
    if row["status"] == "done":
        return False
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE todos SET status = 'done', done_at = ? WHERE id = ?",
        (now, todo_id),
    )
    conn.commit()
    return True


def update_todo(conn: sqlite3.Connection, todo_id: str, **fields: str) -> bool:
    """Update specified fields of a todo. Returns True if updated."""
    row = get_todo(conn, todo_id)
    if row is None:
        return False

    allowed = {"title", "type", "deadline", "content", "status"}
    updates: list[str] = []
    params: list[str] = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)

    if fields.get("status") == "done" and row["status"] != "done":
        updates.append("done_at = ?")
        params.append(datetime.now().isoformat(timespec="seconds"))

    if not updates:
        return False

    params.append(todo_id)
    conn.execute(
        f"UPDATE todos SET {', '.join(updates)} WHERE id = ?", params
    )
    conn.commit()
    return True


def append_attachment(conn: sqlite3.Connection, todo_id: str, rel_path: str) -> bool:
    """Append an attachment path to a todo's attachments JSON array."""
    row = get_todo(conn, todo_id)
    if row is None:
        return False
    current = json.loads(row["attachments"] or "[]")
    current.append(rel_path)
    conn.execute(
        "UPDATE todos SET attachments = ? WHERE id = ?",
        (json.dumps(current), todo_id),
    )
    conn.commit()
    return True


def remove_todo(conn: sqlite3.Connection, todo_id: str) -> bool:
    """Delete a todo. Returns True if deleted."""
    row = get_todo(conn, todo_id)
    if row is None:
        return False
    conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    conn.commit()
    return True


# --- Attachment helpers ---

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_TMP_TIMESTAMP_RE = __import__("re").compile(r"^\d{8}_\d{6}_")


def copy_to_attachments(workspace: Path, source: Path) -> str:
    """Copy file to memory/attachments/YYYY-MM-DD/ with dedup. Returns relative path."""
    att_dir = workspace / "memory" / "attachments"
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_dir = att_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    clean_name = _TMP_TIMESTAMP_RE.sub("", source.name)
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
    return f"attachments/{date_str}/{dest_name}"


# --- Formatting helpers ---

def format_todo_short(row: sqlite3.Row) -> str:
    """One-line format for list view."""
    parts = [f"[{row['id']}]", f"[{row['type']}]", row["title"]]
    if row["deadline"]:
        parts.append(f"(due: {row['deadline']})")
    parts.append(f"@{row['created_by']}" if row["created_by"] else "")
    if row["status"] == "done":
        parts.append(f"✅ {row['done_at'][:10]}" if row["done_at"] else "✅")
    return " ".join(p for p in parts if p)


def format_todo_detail(row: sqlite3.Row) -> str:
    """Multi-line format for detail view."""
    lines = [
        f"### {row['id']}",
        f"- **Type:** {row['type']}",
        f"- **Title:** {row['title']}",
        f"- **Created by:** {row['created_by'] or '—'}",
        f"- **Created at:** {row['created_at']}",
        f"- **Deadline:** {row['deadline'] or '—'}",
        f"- **Status:** {row['status']}",
    ]
    if row["status"] == "done" and row["done_at"]:
        lines.append(f"- **Done at:** {row['done_at']}")

    attachments = json.loads(row["attachments"] or "[]")
    if attachments:
        lines.append("- **Attachments:**")
        for a in attachments:
            name = Path(a).name
            lines.append(f"  - [{name}]({a})")

    if row["content"]:
        lines.append("")
        lines.append(row["content"])

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def export_markdown(rows: list[sqlite3.Row]) -> str:
    """Export todos as Markdown document."""
    lines = [
        "---",
        f"exported: {datetime.now().isoformat(timespec='seconds')}",
        f"count: {len(rows)}",
        "---",
        "",
    ]

    open_todos = [r for r in rows if r["status"] == "open"]
    done_todos = [r for r in rows if r["status"] == "done"]

    if open_todos:
        lines.append("## Open")
        lines.append("")
        for row in open_todos:
            lines.append(format_todo_detail(row))
            lines.append("")

    if done_todos:
        lines.append("## Done")
        lines.append("")
        for row in done_todos:
            lines.append(format_todo_detail(row))
            lines.append("")

    if not rows:
        lines.append("No matching TODOs found.")

    return "\n".join(lines)
