"""Shared cron database utilities for standalone bin scripts.

Provides SQLite-backed cron job management: create, list, update, delete,
and execution history queries.  Stores cron data in the existing memory.db
(adds cron_jobs / cron_meta / cron_history tables).

IMPORTANT: This module must be self-contained (no imports from baobaobot.*)
since bin scripts run outside the package.

Used by: cron-add, cron-list, cron-remove, cron-history
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CRON_SCHEMA = """\
CREATE TABLE IF NOT EXISTS cron_jobs (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    schedule_kind    TEXT NOT NULL,
    schedule_expr    TEXT NOT NULL DEFAULT '',
    schedule_tz      TEXT NOT NULL DEFAULT '',
    schedule_every_s INTEGER NOT NULL DEFAULT 0,
    schedule_at      TEXT NOT NULL DEFAULT '',
    message          TEXT NOT NULL DEFAULT '',
    enabled          INTEGER NOT NULL DEFAULT 1,
    delete_after_run INTEGER NOT NULL DEFAULT 0,
    system           INTEGER NOT NULL DEFAULT 0,
    creator_user_id  INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    next_run_at      REAL,
    running_at       REAL,
    last_run_at      REAL,
    last_status      TEXT NOT NULL DEFAULT '',
    last_error       TEXT NOT NULL DEFAULT '',
    last_duration_s  REAL NOT NULL DEFAULT 0.0,
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    last_summary_offset INTEGER NOT NULL DEFAULT 0,
    last_summary_jsonl  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cron_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cron_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL,
    status      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    duration_s  REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_cron_jobs_enabled ON cron_jobs(enabled);
CREATE INDEX IF NOT EXISTS idx_cron_history_job ON cron_history(job_id);
CREATE INDEX IF NOT EXISTS idx_cron_history_time ON cron_history(started_at);
"""


# ---------------------------------------------------------------------------
# DB connection & migration
# ---------------------------------------------------------------------------


def connect_db(workspace: Path) -> sqlite3.Connection:
    """Open memory.db and ensure cron tables exist. Auto-migrate from JSON."""
    db_path = workspace / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Ensure all cron tables exist (safe to run every time due to IF NOT EXISTS)
    conn.executescript(_CRON_SCHEMA)
    conn.commit()

    # Auto-migrate from jobs.json if DB is empty and JSON exists
    count = conn.execute("SELECT COUNT(*) AS n FROM cron_jobs").fetchone()["n"]
    json_path = workspace / "cron" / "jobs.json"
    if count == 0 and json_path.is_file():
        migrate_from_json(conn, workspace)

    return conn


def migrate_from_json(conn: sqlite3.Connection, workspace: Path) -> int:
    """Read cron/jobs.json, import into DB, rename to .bak. Returns count."""
    json_path = workspace / "cron" / "jobs.json"
    if not json_path.is_file():
        return 0

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    jobs = data.get("jobs", [])
    meta = data.get("workspace_meta", {})

    # Import workspace_meta
    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO cron_meta (key, value) VALUES (?, ?)",
            (f"workspace_meta.{k}", str(v)),
        )

    # Import jobs
    imported = 0
    for job in jobs:
        schedule = job.get("schedule", {})
        state = job.get("state", {})
        conn.execute(
            "INSERT OR REPLACE INTO cron_jobs "
            "(id, name, schedule_kind, schedule_expr, schedule_tz, "
            " schedule_every_s, schedule_at, message, enabled, "
            " delete_after_run, system, creator_user_id, created_at, updated_at, "
            " next_run_at, running_at, last_run_at, last_status, last_error, "
            " last_duration_s, consecutive_errors, last_summary_offset, last_summary_jsonl) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.get("id", uuid.uuid4().hex[:8]),
                job.get("name", ""),
                schedule.get("kind", "cron"),
                schedule.get("expr", ""),
                schedule.get("tz", ""),
                schedule.get("every_seconds", 0),
                schedule.get("at", ""),
                job.get("message", ""),
                1 if job.get("enabled", True) else 0,
                1 if job.get("delete_after_run", False) else 0,
                1 if job.get("system", False) else 0,
                job.get("creator_user_id", 0),
                job.get("created_at", time.time()),
                job.get("updated_at", time.time()),
                state.get("next_run_at"),
                state.get("running_at"),
                state.get("last_run_at"),
                state.get("last_status", ""),
                state.get("last_error", ""),
                state.get("last_duration_s", 0.0),
                state.get("consecutive_errors", 0),
                state.get("last_summary_offset", 0),
                state.get("last_summary_jsonl", ""),
            ),
        )
        imported += 1

    conn.commit()

    # Rename json to .bak
    bak_path = json_path.with_suffix(".json.bak")
    try:
        os.rename(str(json_path), str(bak_path))
    except OSError:
        pass

    return imported


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

_EVERY_RE = re.compile(r"^every:(\d+)([smhd])$", re.IGNORECASE)
_AT_RE = re.compile(r"^at:(.+)$", re.IGNORECASE)
_CRON_RE = re.compile(
    r'^["\']?'
    r"([*/\d,\-]+\s+[*/\d,\-]+\s+[*/\d,\-]+\s+[*/\d,\-]+\s+[*/\d,\-]+)"
    r'["\']?$'
)
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_schedule(text: str) -> tuple[dict | None, str]:
    """Parse schedule string -> (schedule_dict, error)."""
    text = text.strip()
    if not text:
        return None, "Empty schedule string"

    m = _EVERY_RE.match(text)
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower()
        seconds = value * _UNIT_TO_SECONDS[unit]
        if seconds <= 0:
            return None, "Interval must be positive"
        return {"kind": "every", "every_seconds": seconds}, ""

    m = _AT_RE.match(text)
    if m:
        at_str = m.group(1).strip()
        return {"kind": "at", "at": at_str}, ""

    m = _CRON_RE.match(text)
    if m:
        expr = m.group(1).strip()
        return {"kind": "cron", "expr": expr}, ""

    return None, f"Unrecognized schedule format: {text}"


def compute_next_run(schedule: dict, after_ts: float, tz_name: str = "") -> float | None:
    """Compute next run Unix timestamp, or None."""
    kind = schedule.get("kind")
    if kind == "at":
        return _compute_at(schedule, after_ts, tz_name)
    elif kind == "every":
        secs = schedule.get("every_seconds", 0)
        return (after_ts + secs) if secs > 0 else None
    elif kind == "cron":
        return _compute_cron(schedule, after_ts, tz_name)
    return None


def _compute_at(schedule: dict, after_ts: float, tz_name: str) -> float | None:
    at_str = schedule.get("at", "")
    if not at_str:
        return None
    try:
        dt = datetime.fromisoformat(at_str)
        if dt.tzinfo is None:
            tz = _resolve_tz(tz_name)
            dt = dt.replace(tzinfo=tz or timezone.utc)
        ts = dt.timestamp()
        return ts if ts > after_ts else None
    except ValueError:
        return None


def _compute_cron(schedule: dict, after_ts: float, tz_name: str) -> float | None:
    expr = schedule.get("expr", "")
    if not expr:
        return None
    try:
        from croniter import croniter
    except ImportError:
        return after_ts + 60
    tz = _resolve_tz(schedule.get("tz", "") or tz_name)
    try:
        if tz:
            dt_after = datetime.fromtimestamp(after_ts, tz=tz)
        else:
            dt_after = datetime.fromtimestamp(after_ts, tz=timezone.utc)
        cron = croniter(expr, dt_after)
        next_dt = cron.get_next(datetime)
        return next_dt.timestamp()
    except (ValueError, KeyError):
        return None


def _resolve_tz(tz_name: str):
    if not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except (KeyError, ValueError, ImportError):
        return None


# ---------------------------------------------------------------------------
# Schedule dict <-> DB columns
# ---------------------------------------------------------------------------


def schedule_to_cols(schedule: dict) -> dict:
    """Convert a schedule dict to DB column values."""
    return {
        "schedule_kind": schedule.get("kind", ""),
        "schedule_expr": schedule.get("expr", ""),
        "schedule_tz": schedule.get("tz", ""),
        "schedule_every_s": schedule.get("every_seconds", 0),
        "schedule_at": schedule.get("at", ""),
    }


def cols_to_schedule(row: sqlite3.Row) -> dict:
    """Convert DB row columns back to a schedule dict."""
    kind = row["schedule_kind"]
    d: dict = {"kind": kind}
    if kind == "cron":
        d["expr"] = row["schedule_expr"]
        if row["schedule_tz"]:
            d["tz"] = row["schedule_tz"]
    elif kind == "every":
        d["every_seconds"] = row["schedule_every_s"]
    elif kind == "at":
        d["at"] = row["schedule_at"]
    return d


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------


def resolve_workspace(args_workspace: str | None = None) -> Path:
    """Resolve workspace directory: explicit arg > walk up cwd."""
    if args_workspace:
        return Path(args_workspace)
    cwd = Path.cwd()
    for d in [cwd, *cwd.parents]:
        if (d / "memory").is_dir() or (d / "memory.db").is_file():
            return d
    print(
        "Cannot determine workspace. Use --workspace or run from a workspace dir.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_job(
    conn: sqlite3.Connection,
    *,
    name: str,
    schedule: dict,
    message: str,
    tz: str = "",
    creator_user_id: int = 0,
) -> dict:
    """Insert a new cron job. Returns the job row as dict."""
    now = time.time()
    job_id = uuid.uuid4().hex[:8]
    delete_after = schedule.get("kind") == "at"
    next_run = compute_next_run(schedule, now, tz)
    cols = schedule_to_cols(schedule)

    conn.execute(
        "INSERT INTO cron_jobs "
        "(id, name, schedule_kind, schedule_expr, schedule_tz, "
        " schedule_every_s, schedule_at, message, enabled, "
        " delete_after_run, creator_user_id, created_at, updated_at, next_run_at) "
        "VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
        (
            job_id,
            name,
            cols["schedule_kind"],
            cols["schedule_expr"],
            cols["schedule_tz"],
            cols["schedule_every_s"],
            cols["schedule_at"],
            message,
            1 if delete_after else 0,
            creator_user_id,
            now,
            now,
            next_run,
        ),
    )
    conn.commit()
    return {
        "id": job_id,
        "name": name,
        "schedule": schedule,
        "message": message,
        "delete_after_run": delete_after,
        "next_run_at": next_run,
    }


def list_jobs(
    conn: sqlite3.Connection,
    *,
    enabled_only: bool = False,
    include_system: bool = True,
) -> list[sqlite3.Row]:
    """List cron jobs."""
    conditions: list[str] = []
    params: list = []
    if enabled_only:
        conditions.append("enabled = 1")
    if not include_system:
        conditions.append("system = 0")
    sql = "SELECT * FROM cron_jobs"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at"
    return conn.execute(sql, params).fetchall()


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    """Get a single job by ID."""
    return conn.execute(
        "SELECT * FROM cron_jobs WHERE id = ?", (job_id,)
    ).fetchone()


def remove_job(conn: sqlite3.Connection, job_id: str) -> bool:
    """Delete a job. Returns True if deleted."""
    row = get_job(conn, job_id)
    if row is None:
        return False
    conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
    conn.commit()
    return True


def update_job(conn: sqlite3.Connection, job_id: str, **fields) -> bool:
    """Update specified fields of a job. Returns True if updated."""
    row = get_job(conn, job_id)
    if row is None:
        return False

    allowed = {
        "name", "message", "enabled", "schedule_kind", "schedule_expr",
        "schedule_tz", "schedule_every_s", "schedule_at",
        "next_run_at", "running_at", "last_run_at", "last_status",
        "last_error", "last_duration_s", "consecutive_errors",
        "last_summary_offset", "last_summary_jsonl",
    }
    updates: list[str] = []
    params: list = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)

    updates.append("updated_at = ?")
    params.append(time.time())

    if not updates:
        return False

    params.append(job_id)
    conn.execute(
        f"UPDATE cron_jobs SET {', '.join(updates)} WHERE id = ?", params
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Cron meta (workspace_meta)
# ---------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a cron_meta value by key."""
    row = conn.execute(
        "SELECT value FROM cron_meta WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a cron_meta value."""
    conn.execute(
        "INSERT OR REPLACE INTO cron_meta (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def get_workspace_meta(conn: sqlite3.Connection) -> dict:
    """Get workspace_meta as a dict."""
    result: dict = {}
    rows = conn.execute(
        "SELECT key, value FROM cron_meta WHERE key LIKE 'workspace_meta.%'"
    ).fetchall()
    for row in rows:
        k = row["key"].removeprefix("workspace_meta.")
        # Try to parse as int
        try:
            result[k] = int(row["value"])
        except ValueError:
            result[k] = row["value"]
    return result


def set_workspace_meta(conn: sqlite3.Connection, meta: dict) -> None:
    """Set workspace_meta dict."""
    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO cron_meta (key, value) VALUES (?, ?)",
            (f"workspace_meta.{k}", str(v)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def add_history(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    started_at: float,
    finished_at: float | None = None,
    status: str = "",
    error: str = "",
    duration_s: float = 0.0,
) -> int:
    """Insert a history record. Returns the row ID."""
    cursor = conn.execute(
        "INSERT INTO cron_history (job_id, started_at, finished_at, status, error, duration_s) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, started_at, finished_at, status, error, duration_s),
    )
    conn.commit()
    return cursor.lastrowid or 0


def list_history(
    conn: sqlite3.Connection,
    *,
    job_id: str | None = None,
    days: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Query execution history."""
    conditions: list[str] = []
    params: list = []

    if job_id:
        conditions.append("job_id = ?")
        params.append(job_id)
    if days is not None:
        cutoff = time.time() - days * 86400
        conditions.append("started_at >= ?")
        params.append(cutoff)
    if status:
        conditions.append("status = ?")
        params.append(status)

    sql = "SELECT * FROM cron_history"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    return conn.execute(sql, params).fetchall()


def cleanup_history(conn: sqlite3.Connection, days: int = 90) -> int:
    """Delete history records older than N days. Returns count deleted."""
    cutoff = time.time() - days * 86400
    cursor = conn.execute(
        "DELETE FROM cron_history WHERE started_at < ?", (cutoff,)
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_schedule(row: sqlite3.Row) -> str:
    """Format schedule columns for display."""
    kind = row["schedule_kind"]
    if kind == "cron":
        tz = row["schedule_tz"]
        tz_part = f" ({tz})" if tz else ""
        return f"{row['schedule_expr']}{tz_part}"
    elif kind == "every":
        secs = row["schedule_every_s"]
        if secs >= 86400 and secs % 86400 == 0:
            return f"every {secs // 86400}d"
        if secs >= 3600 and secs % 3600 == 0:
            return f"every {secs // 3600}h"
        if secs >= 60 and secs % 60 == 0:
            return f"every {secs // 60}m"
        return f"every {secs}s"
    elif kind == "at":
        return f"at {row['schedule_at']}"
    return f"unknown({kind})"


def format_ts(ts) -> str:
    """Format Unix timestamp for display."""
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
