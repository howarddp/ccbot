"""SystemScheduler — runs system tasks via `claude -p` subprocesses.

Handles tasks that require a Claude Code session (summary, consolidation,
heartbeat) by spawning one-shot `claude -p` subprocesses rather than
sending messages to live tmux windows.

Current tasks:
  - Hourly summary: reads JSONL transcript, writes to memory/summaries/

State is stored in each workspace's memory.db `cron_meta` table.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .session import SessionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Check interval for the timer loop
_TICK_INTERVAL_S = 60.0

# How often to run summary per workspace
_SUMMARY_INTERVAL_S = 3600

# Subprocess timeout for claude -p
_SUBPROCESS_TIMEOUT_S = 300

# Max concurrent claude -p processes
_MAX_CONCURRENT = 2

# Idle threshold: JSONL must not have changed in the last N seconds
_IDLE_THRESHOLD_S = 300  # 5 minutes

# cron_meta key for last summary time
_META_KEY_LAST_SUMMARY_TIME = "system_scheduler.last_summary_time"

# cron_meta key for last summary JSONL path (to detect session changes)
_META_KEY_LAST_SUMMARY_JSONL = "system_scheduler.last_summary_jsonl"

# cron_meta key for last summary JSONL byte offset (to detect new content)
_META_KEY_LAST_SUMMARY_OFFSET = "system_scheduler.last_summary_offset"

# cron_meta key for next summary run timestamp
_META_KEY_NEXT_SUMMARY_RUN = "system_scheduler.next_summary_run"

# cron_meta key for consecutive error count
_META_KEY_SUMMARY_ERRORS = "system_scheduler.summary_consecutive_errors"

# Notify admin after this many consecutive errors
_ADMIN_NOTIFY_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_output(stdout: str) -> tuple[str, str | None]:
    """Parse claude -p stdout.

    Returns:
        ("silent", None) — nothing to report
        ("notify", content) — content to send to users
    """
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[SILENT]":
            return "silent", None
        if stripped == "[NOTIFY]":
            content = "\n".join(lines[i + 1:]).strip()
            return "notify", content if content else None
    # No recognised marker — treat as silent to avoid noise
    return "silent", None


def _get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM cron_meta WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else default


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cron_meta (key, value) VALUES (?, ?)",
        (key, value),
    )


def _connect(ws_dir: Path) -> sqlite3.Connection:
    db_path = ws_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cron_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    return conn


# ---------------------------------------------------------------------------
# SystemScheduler
# ---------------------------------------------------------------------------

# on_notify callback type: (user_id, chat_id, thread_id, text) -> None
OnNotifyCallback = Callable[[int, int, int, str], Awaitable[None]]


class SystemScheduler:
    """Runs system tasks via `claude -p` subprocesses."""

    def __init__(
        self,
        *,
        session_manager: SessionManager,
        iter_workspace_dirs: Callable[[], list[Path]],
        locale: str = "en-US",
        timezone: str = "",
        agent_name: str = "",
        admin_user_ids: list[int] | None = None,
        on_notify: OnNotifyCallback,
    ) -> None:
        self._session_manager = session_manager
        self._iter_workspace_dirs = iter_workspace_dirs
        self._locale = locale
        self._timezone = timezone or self._detect_timezone()
        self._agent_name = agent_name
        self._admin_user_ids = admin_user_ids or []
        self._on_notify = on_notify

        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        self._running = False
        self._timer_task: asyncio.Task[None] | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

        # Load prompt template once
        template_path = Path(__file__).parent / "templates" / "summary_prompt.md"
        self._summary_template = template_path.read_text(encoding="utf-8")

    @staticmethod
    def _detect_timezone() -> str:
        """Detect system timezone, fallback to UTC."""
        import subprocess
        try:
            result = subprocess.run(
                ["readlink", "/etc/localtime"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "zoneinfo/" in result.stdout:
                return result.stdout.strip().split("zoneinfo/")[-1]
        except Exception:
            pass
        return "UTC"

    # --- Lifecycle ---

    async def start(self) -> None:
        logger.info("SystemScheduler starting...")
        self._running = True
        self._timer_task = asyncio.create_task(self._timer_loop())
        logger.info("SystemScheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
            self._timer_task = None
        logger.info("SystemScheduler stopped")

    # --- Public API ---

    async def trigger_summary(self, workspace_name: str) -> bool:
        """Trigger an immediate summary for a workspace.

        Ignores the scheduled next_run_at — always runs if there's new content.
        Returns True if a summary was run, False if skipped (no new content).
        """
        ws_dir = self._find_workspace_dir(workspace_name)
        if not ws_dir:
            logger.warning("trigger_summary: workspace dir not found for %r", workspace_name)
            return False

        window_id = self._resolve_window(workspace_name)
        if not window_id:
            logger.warning("trigger_summary: window not found for %r", workspace_name)
            return False

        jsonl_path = self._get_jsonl_path(window_id, ws_dir)
        if not jsonl_path:
            logger.warning("trigger_summary: JSONL not found for window %s", window_id)
            return False

        if not self._has_new_content(ws_dir, jsonl_path):
            logger.info("trigger_summary: no new content for %r", workspace_name)
            return False

        async with self._semaphore:
            return await self._run_summary(workspace_name, ws_dir, window_id, jsonl_path)

    # --- Timer loop ---

    async def _timer_loop(self) -> None:
        while self._running:
            try:
                await self._check_all_workspaces()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SystemScheduler timer loop error")
            try:
                await asyncio.sleep(_TICK_INTERVAL_S)
            except asyncio.CancelledError:
                break

    async def _check_all_workspaces(self) -> None:
        now = time.time()
        tasks = []
        for ws_dir in self._iter_workspace_dirs():
            ws_name = ws_dir.name.removeprefix("workspace_")
            if not self._is_summary_due(ws_dir, now):
                continue
            window_id = self._resolve_window(ws_name)
            if not window_id:
                continue
            jsonl_path = self._get_jsonl_path(window_id, ws_dir)
            if not jsonl_path:
                continue
            if not self._has_new_content(ws_dir, jsonl_path):
                # No new content — reschedule and skip
                self._set_next_run(ws_dir, now + _SUMMARY_INTERVAL_S)
                continue
            # Check idle: don't summarize while Claude is actively writing
            try:
                mtime = jsonl_path.stat().st_mtime
                if (now - mtime) < _IDLE_THRESHOLD_S:
                    continue
            except OSError:
                continue

            tasks.append((ws_name, ws_dir, window_id, jsonl_path))

        for ws_name, ws_dir, window_id, jsonl_path in tasks:
            task = asyncio.create_task(
                self._run_with_semaphore(ws_name, ws_dir, window_id, jsonl_path)
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    async def _run_with_semaphore(
        self,
        workspace_name: str,
        ws_dir: Path,
        window_id: str,
        jsonl_path: Path,
    ) -> None:
        async with self._semaphore:
            await self._run_summary(workspace_name, ws_dir, window_id, jsonl_path)

    # --- Core summary logic ---

    async def _run_summary(
        self,
        workspace_name: str,
        ws_dir: Path,
        window_id: str,
        jsonl_path: Path,
    ) -> bool:
        """Run claude -p for summary, parse output, deliver notifications.

        Returns True on success (even if [SILENT]), False on error.
        """
        now = time.time()
        last_summary_time = self._get_last_summary_time(ws_dir)
        today_date = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        summary_path = ws_dir / "memory" / "summaries" / f"{today_date}.md"

        from .utils import baobaobot_dir
        memory_save_bin = baobaobot_dir() / "shared" / "bin" / "memory-save"

        prompt = self._summary_template.format(
            jsonl_path=str(jsonl_path),
            workspace_path=str(ws_dir),
            last_summary_time=last_summary_time,
            locale=self._locale,
            summary_path=str(summary_path),
            today_date=today_date,
            memory_save_bin=str(memory_save_bin),
            timezone=self._timezone,
        )

        logger.info(
            "SystemScheduler: running summary for %s (last=%s)",
            workspace_name,
            last_summary_time,
        )

        try:
            stdout = await self._run_claude_p(prompt, cwd=ws_dir)
        except asyncio.TimeoutError:
            logger.warning("Summary timeout for %s", workspace_name)
            self._record_error(ws_dir, "timeout")
            return False
        except Exception as e:
            logger.warning("Summary failed for %s: %s", workspace_name, e)
            self._record_error(ws_dir, str(e))
            await self._maybe_notify_admin(workspace_name, ws_dir, str(e))
            return False

        action, content = _parse_output(stdout)

        # Update state in a single transaction
        self._update_state_after_summary(ws_dir, now, jsonl_path)

        if action == "notify" and content:
            await self._deliver_notify(workspace_name, window_id, content)

        logger.info(
            "SystemScheduler: summary done for %s → %s", workspace_name, action
        )
        return True

    async def _run_claude_p(self, prompt: str, cwd: Path) -> str:
        """Run `claude -p` as subprocess, return stdout."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--no-session-persistence",
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_SUBPROCESS_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise

        return stdout_bytes.decode("utf-8", errors="replace")

    # --- Delivery ---

    async def _deliver_notify(
        self, workspace_name: str, window_id: str, content: str
    ) -> None:
        """Deliver summary to all users bound to this workspace's window."""
        delivered = 0
        for user_id, thread_id, wid in self._session_manager.iter_thread_bindings():
            if wid != window_id:
                continue
            key = f"{user_id}:{thread_id}"
            chat_id = self._session_manager.group_chat_ids.get(key)
            if not chat_id:
                logger.debug(
                    "SystemScheduler: no chat_id for user %d thread %d", user_id, thread_id
                )
                continue
            try:
                await self._on_notify(user_id, chat_id, thread_id, content)
                delivered += 1
                logger.info(
                    "SystemScheduler: delivered summary to user %d thread %d chat %d",
                    user_id, thread_id, chat_id,
                )
            except Exception:
                logger.warning(
                    "Failed to deliver summary to user %d thread %d",
                    user_id,
                    thread_id,
                    exc_info=True,
                )
        if delivered == 0:
            logger.warning(
                "SystemScheduler: no delivery targets found for workspace=%s window=%s",
                workspace_name, window_id,
            )

    async def _maybe_notify_admin(
        self, workspace_name: str, ws_dir: Path, error: str
    ) -> None:
        errors = self._get_consecutive_errors(ws_dir)
        if errors < _ADMIN_NOTIFY_THRESHOLD:
            return
        msg = (
            f"⚠️ SystemScheduler: summary failed {errors} times for "
            f"`{workspace_name}`\nError: {error}"
        )
        for admin_id in self._admin_user_ids:
            # Try to find a chat_id for the admin
            for user_id, thread_id, _ in self._session_manager.iter_thread_bindings():
                if user_id != admin_id:
                    continue
                key = f"{user_id}:{thread_id}"
                chat_id = self._session_manager.group_chat_ids.get(key)
                if chat_id:
                    try:
                        await self._on_notify(admin_id, chat_id, thread_id, msg)
                    except Exception:
                        pass
                    break

    # --- State helpers (cron_meta in memory.db) ---

    def _update_state_after_summary(
        self, ws_dir: Path, now: float, jsonl_path: Path
    ) -> None:
        """Update all state keys after a successful summary in one transaction."""
        dt = datetime.fromtimestamp(now, tz=timezone.utc)
        time_val = dt.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            size = jsonl_path.stat().st_size
        except OSError:
            size = 0
        try:
            conn = _connect(ws_dir)
            try:
                _set_meta(conn, _META_KEY_LAST_SUMMARY_TIME, time_val)
                _set_meta(conn, _META_KEY_LAST_SUMMARY_JSONL, str(jsonl_path))
                _set_meta(conn, _META_KEY_LAST_SUMMARY_OFFSET, str(size))
                _set_meta(conn, _META_KEY_NEXT_SUMMARY_RUN, str(now + _SUMMARY_INTERVAL_S))
                _set_meta(conn, _META_KEY_SUMMARY_ERRORS, "0")
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.warning("Failed to update state after summary for %s", ws_dir)

    def _get_last_summary_time(self, ws_dir: Path) -> str:
        """Return last summary time as ISO string, or epoch start."""
        try:
            conn = _connect(ws_dir)
            try:
                val = _get_meta(conn, _META_KEY_LAST_SUMMARY_TIME)
                return val if val else "1970-01-01T00:00:00"
            finally:
                conn.close()
        except Exception:
            return "1970-01-01T00:00:00"

    def _has_new_content(self, ws_dir: Path, jsonl_path: Path) -> bool:
        """Return True if JSONL has new content since last summary."""
        try:
            current_size = jsonl_path.stat().st_size
        except OSError:
            return False

        try:
            conn = _connect(ws_dir)
            try:
                last_jsonl = _get_meta(conn, _META_KEY_LAST_SUMMARY_JSONL)
                last_offset_str = _get_meta(conn, _META_KEY_LAST_SUMMARY_OFFSET, "0")
            finally:
                conn.close()
        except Exception:
            return True  # Assume new content on DB error

        # Session changed (new JSONL path) — reset offset
        if last_jsonl and last_jsonl != str(jsonl_path):
            return True

        try:
            last_offset = int(last_offset_str)
        except ValueError:
            last_offset = 0

        return current_size > last_offset

    def _is_summary_due(self, ws_dir: Path, now: float) -> bool:
        try:
            conn = _connect(ws_dir)
            try:
                val = _get_meta(conn, _META_KEY_NEXT_SUMMARY_RUN, "0")
            finally:
                conn.close()
        except Exception:
            return True

        try:
            next_run = float(val)
        except ValueError:
            next_run = 0.0

        return now >= next_run

    def _set_next_run(self, ws_dir: Path, next_run: float) -> None:
        try:
            conn = _connect(ws_dir)
            try:
                _set_meta(conn, _META_KEY_NEXT_SUMMARY_RUN, str(next_run))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _record_error(self, ws_dir: Path, error: str) -> None:
        try:
            conn = _connect(ws_dir)
            try:
                errors = int(_get_meta(conn, _META_KEY_SUMMARY_ERRORS, "0")) + 1
                _set_meta(conn, _META_KEY_SUMMARY_ERRORS, str(errors))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _reset_errors(self, ws_dir: Path) -> None:
        try:
            conn = _connect(ws_dir)
            try:
                _set_meta(conn, _META_KEY_SUMMARY_ERRORS, "0")
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _get_consecutive_errors(self, ws_dir: Path) -> int:
        try:
            conn = _connect(ws_dir)
            try:
                return int(_get_meta(conn, _META_KEY_SUMMARY_ERRORS, "0"))
            finally:
                conn.close()
        except Exception:
            return 0

    # --- Window / JSONL resolution ---

    def _resolve_window(self, workspace_name: str) -> str | None:
        agent_prefix = f"{self._agent_name}/" if self._agent_name else ""
        # Check thread bindings first (forum/topic mode)
        for _, _, window_id in self._session_manager.iter_thread_bindings():
            display = self._session_manager.get_display_name(window_id)
            if agent_prefix:
                display = display.removeprefix(agent_prefix)
            if display == workspace_name:
                return window_id
        # Fallback: check all known windows by display name (group mode)
        for wid, display in self._session_manager.window_display_names.items():
            if agent_prefix:
                display = display.removeprefix(agent_prefix)
            if display == workspace_name:
                return wid
        return None

    def _find_workspace_dir(self, workspace_name: str) -> Path | None:
        for ws_dir in self._iter_workspace_dirs():
            if ws_dir.name.removeprefix("workspace_") == workspace_name:
                return ws_dir
        return None

    def _get_jsonl_path(self, window_id: str, ws_dir: Path) -> Path | None:
        """Find the JSONL transcript file for a window's current session."""
        state = self._session_manager.get_window_state(window_id)
        if not state.session_id:
            return None

        projects_dir = Path.home() / ".claude" / "projects"
        if not projects_dir.exists():
            return None

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            jsonl_file = project_dir / f"{state.session_id}.jsonl"
            if jsonl_file.exists():
                return jsonl_file
        return None

    @property
    def is_running(self) -> bool:
        return self._running
