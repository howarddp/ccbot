"""SQLite persistence for per-workspace cron stores.

Stores cron jobs in workspace/memory.db (cron_jobs, cron_meta, cron_history
tables).  On first connect, auto-migrates from legacy cron/jobs.json if
present.

Retains the same CronStoreFile / CronJob / CronSchedule dataclass interface
so that service.py needs minimal changes.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from .types import CronJob, CronJobState, CronSchedule, CronStoreFile, WorkspaceMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema (must stay in sync with shared/bin/_cron_common.py)
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
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Ensure all cron tables exist (safe to run every time due to IF NOT EXISTS)."""
    conn.executescript(_CRON_SCHEMA)
    conn.commit()


def _connect(workspace_dir: Path) -> sqlite3.Connection:
    """Open memory.db with row_factory and ensure tables."""
    db_path = workspace_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _migrate_from_json(conn: sqlite3.Connection, workspace_dir: Path) -> None:
    """Import from cron/jobs.json into DB if table is empty and JSON exists."""
    json_path = workspace_dir / "cron" / "jobs.json"
    if not json_path.is_file():
        return
    count = conn.execute("SELECT COUNT(*) AS n FROM cron_jobs").fetchone()["n"]
    if count > 0:
        return

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s for migration: %s", json_path, e)
        return

    jobs = data.get("jobs", [])
    meta = data.get("workspace_meta", {})

    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO cron_meta (key, value) VALUES (?, ?)",
            (f"workspace_meta.{k}", str(v)),
        )

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
                job.get("id", ""),
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

    conn.commit()

    bak_path = json_path.with_suffix(".json.bak")
    try:
        os.rename(str(json_path), str(bak_path))
        logger.info("Migrated %d jobs from %s → DB, renamed to .bak", len(jobs), json_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Row ↔ dataclass conversion
# ---------------------------------------------------------------------------


def _row_to_job(row: sqlite3.Row) -> CronJob:
    """Convert a DB row to CronJob dataclass."""
    kind = row["schedule_kind"]
    schedule = CronSchedule(
        kind=kind,
        expr=row["schedule_expr"],
        tz=row["schedule_tz"],
        every_seconds=row["schedule_every_s"],
        at=row["schedule_at"],
    )
    state = CronJobState(
        next_run_at=row["next_run_at"],
        running_at=row["running_at"],
        last_run_at=row["last_run_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
        last_duration_s=row["last_duration_s"],
        consecutive_errors=row["consecutive_errors"],
        last_summary_offset=row["last_summary_offset"],
        last_summary_jsonl=row["last_summary_jsonl"],
    )
    return CronJob(
        id=row["id"],
        name=row["name"],
        schedule=schedule,
        message=row["message"],
        enabled=bool(row["enabled"]),
        delete_after_run=bool(row["delete_after_run"]),
        system=bool(row["system"]),
        creator_user_id=row["creator_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        state=state,
    )


def _job_to_params(job: CronJob) -> tuple:
    """Convert CronJob dataclass to a tuple for INSERT/REPLACE."""
    return (
        job.id,
        job.name,
        job.schedule.kind,
        job.schedule.expr,
        job.schedule.tz,
        job.schedule.every_seconds,
        job.schedule.at,
        job.message,
        1 if job.enabled else 0,
        1 if job.delete_after_run else 0,
        1 if job.system else 0,
        job.creator_user_id,
        job.created_at,
        job.updated_at,
        job.state.next_run_at,
        job.state.running_at,
        job.state.last_run_at,
        job.state.last_status,
        job.state.last_error,
        job.state.last_duration_s,
        job.state.consecutive_errors,
        job.state.last_summary_offset,
        job.state.last_summary_jsonl,
    )


_UPSERT_SQL = (
    "INSERT OR REPLACE INTO cron_jobs "
    "(id, name, schedule_kind, schedule_expr, schedule_tz, "
    " schedule_every_s, schedule_at, message, enabled, "
    " delete_after_run, system, creator_user_id, created_at, updated_at, "
    " next_run_at, running_at, last_run_at, last_status, last_error, "
    " last_duration_s, consecutive_errors, last_summary_offset, last_summary_jsonl) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


# ---------------------------------------------------------------------------
# Public API (same interface as before)
# ---------------------------------------------------------------------------


def load_store(workspace_dir: Path) -> CronStoreFile:
    """Load cron store from DB. Auto-migrates from JSON on first call."""
    conn = _connect(workspace_dir)
    try:
        _migrate_from_json(conn, workspace_dir)

        # Load workspace_meta from cron_meta
        meta = WorkspaceMeta()
        meta_rows = conn.execute(
            "SELECT key, value FROM cron_meta WHERE key LIKE 'workspace_meta.%'"
        ).fetchall()
        meta_dict: dict = {}
        for r in meta_rows:
            k = r["key"].removeprefix("workspace_meta.")
            try:
                meta_dict[k] = int(r["value"])
            except ValueError:
                meta_dict[k] = r["value"]
        if meta_dict:
            meta = WorkspaceMeta.from_dict(meta_dict)

        # Load jobs
        rows = conn.execute("SELECT * FROM cron_jobs ORDER BY created_at").fetchall()
        jobs = [_row_to_job(r) for r in rows]

        return CronStoreFile(workspace_meta=meta, jobs=jobs)
    finally:
        conn.close()


def save_store(workspace_dir: Path, store: CronStoreFile) -> None:
    """Write cron store to DB (UPSERT jobs, UPDATE meta)."""
    conn = _connect(workspace_dir)
    try:
        # Upsert workspace_meta
        meta_dict = store.workspace_meta.to_dict()
        for k, v in meta_dict.items():
            conn.execute(
                "INSERT OR REPLACE INTO cron_meta (key, value) VALUES (?, ?)",
                (f"workspace_meta.{k}", str(v)),
            )

        # Collect current job IDs in the store
        store_ids = {j.id for j in store.jobs}

        # Delete jobs that no longer exist in the store
        existing_ids = {
            r["id"]
            for r in conn.execute("SELECT id FROM cron_jobs").fetchall()
        }
        for old_id in existing_ids - store_ids:
            conn.execute("DELETE FROM cron_jobs WHERE id = ?", (old_id,))

        # Upsert all jobs
        for job in store.jobs:
            conn.execute(_UPSERT_SQL, _job_to_params(job))

        conn.commit()
    finally:
        conn.close()


def store_mtime(workspace_dir: Path) -> float:
    """Return mtime of memory.db, or 0.0 if not found."""
    db_path = workspace_dir / "memory.db"
    try:
        return db_path.stat().st_mtime
    except OSError:
        return 0.0


def record_history(
    workspace_dir: Path,
    *,
    job_id: str,
    started_at: float,
    finished_at: float | None = None,
    status: str = "",
    error: str = "",
    duration_s: float = 0.0,
) -> None:
    """Insert a cron_history record."""
    conn = _connect(workspace_dir)
    try:
        conn.execute(
            "INSERT INTO cron_history (job_id, started_at, finished_at, status, error, duration_s) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, started_at, finished_at, status, error, duration_s),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_history(workspace_dir: Path, days: int = 90) -> int:
    """Delete history records older than N days. Returns count deleted."""
    conn = _connect(workspace_dir)
    try:
        cutoff = time.time() - days * 86400
        cursor = conn.execute(
            "DELETE FROM cron_history WHERE started_at < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
