"""CronService — global singleton managing all workspace cron jobs.

Scans workspaces on startup, runs a single asyncio timer loop,
and dispatches due jobs to tmux windows via session_manager.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from ..config import config
from ..persona.profile import get_user_display_name
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .schedule import compute_next_run
from .store import load_store, save_store, store_mtime
from .types import CronJob, CronJobState, CronSchedule, CronStoreFile, WorkspaceMeta

logger = logging.getLogger(__name__)

# Exponential backoff caps for consecutive errors
_BACKOFF_SECONDS = [30, 60, 300, 900, 3600]

# If a job has been running for more than this, consider it stuck
_STUCK_TIMEOUT_S = 7200  # 2 hours

# Maximum sleep between ticks
_MAX_TICK_INTERVAL = 60.0

# Minimum sleep between ticks
_MIN_TICK_INTERVAL = 5.0


def _backoff_delay(consecutive_errors: int) -> float:
    """Return backoff delay in seconds for given error count."""
    if consecutive_errors <= 0:
        return 0
    idx = min(consecutive_errors - 1, len(_BACKOFF_SECONDS) - 1)
    return _BACKOFF_SECONDS[idx]


class CronService:
    """Global singleton managing all workspace cron jobs."""

    def __init__(self) -> None:
        self._stores: dict[str, CronStoreFile] = {}  # workspace_name → store
        self._workspace_dirs: dict[str, Path] = {}  # workspace_name → dir
        self._mtimes: dict[str, float] = {}  # workspace_name → last seen mtime
        self._timer_task: asyncio.Task[None] | None = None
        self._running = False
        self._wake_event: asyncio.Event = asyncio.Event()

    # --- Lifecycle ---

    async def start(self) -> None:
        """Scan all workspaces, load stores, catch up missed jobs, start timer."""
        logger.info("CronService starting...")
        self._scan_workspaces()
        now = time.time()

        # Catch-up: execute jobs that were due while bot was offline
        # Cache resolved windows per workspace to avoid redundant recreation
        catchup_count = 0
        window_cache: dict[str, str | None] = {}
        for ws_name, store in self._stores.items():
            for job in store.jobs:
                if not job.enabled:
                    continue
                # Clear stuck jobs
                if (
                    job.state.running_at
                    and (now - job.state.running_at) > _STUCK_TIMEOUT_S
                ):
                    job.state.running_at = None
                    job.state.last_status = "error"
                    job.state.last_error = "stuck (timeout)"
                    job.state.consecutive_errors += 1
                # Catch up missed runs
                if job.state.next_run_at and job.state.next_run_at < now:
                    # Pre-resolve window once per workspace
                    if ws_name not in window_cache:
                        wid = self._resolve_window(ws_name)
                        if not wid:
                            wid = await self._recreate_window(ws_name)
                        window_cache[ws_name] = wid
                    catchup_count += 1
                    await self._execute_job(
                        ws_name, job, window_id=window_cache[ws_name]
                    )
            save_store(self._workspace_dirs[ws_name], store)

        if catchup_count:
            logger.info("Caught up %d missed cron job(s)", catchup_count)

        self._running = True
        self._timer_task = asyncio.create_task(self._timer_loop())
        logger.info(
            "CronService started: %d workspace(s), %d total job(s)",
            len(self._stores),
            sum(len(s.jobs) for s in self._stores.values()),
        )

    async def stop(self) -> None:
        """Cancel timer and save all stores."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
            self._timer_task = None
        # Save all stores on shutdown
        for ws_name, store in self._stores.items():
            ws_dir = self._workspace_dirs.get(ws_name)
            if ws_dir:
                save_store(ws_dir, store)
        logger.info("CronService stopped")

    # --- CRUD ---

    async def add_job(
        self,
        workspace_name: str,
        name: str,
        schedule: CronSchedule,
        message: str,
        meta: WorkspaceMeta | None = None,
        creator_user_id: int = 0,
    ) -> CronJob:
        """Add a new cron job to a workspace."""
        store = self._ensure_store(workspace_name)
        if meta:
            store.workspace_meta = meta

        now = time.time()
        job_id = uuid.uuid4().hex[:8]
        delete_after = schedule.kind == "at"

        next_run = compute_next_run(schedule, now, config.cron_default_tz)
        job = CronJob(
            id=job_id,
            name=name,
            schedule=schedule,
            message=message,
            enabled=True,
            delete_after_run=delete_after,
            creator_user_id=creator_user_id,
            created_at=now,
            updated_at=now,
            state=CronJobState(next_run_at=next_run),
        )
        store.jobs.append(job)
        self._save(workspace_name)
        self._wake_timer()
        logger.info(
            "Added cron job %s '%s' to %s (next: %s)",
            job_id,
            name,
            workspace_name,
            next_run,
        )
        return job

    async def remove_job(self, workspace_name: str, job_id: str) -> bool:
        """Remove a cron job by ID."""
        store = self._stores.get(workspace_name)
        if not store:
            return False
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        if len(store.jobs) < before:
            self._save(workspace_name)
            return True
        return False

    async def list_jobs(self, workspace_name: str) -> list[CronJob]:
        """List all cron jobs for a workspace."""
        store = self._stores.get(workspace_name)
        return list(store.jobs) if store else []

    async def enable_job(self, workspace_name: str, job_id: str) -> CronJob | None:
        """Enable a cron job."""
        job = self._find_job(workspace_name, job_id)
        if not job:
            return None
        job.enabled = True
        job.updated_at = time.time()
        # Recompute next run
        job.state.next_run_at = compute_next_run(
            job.schedule, time.time(), config.cron_default_tz
        )
        job.state.consecutive_errors = 0
        self._save(workspace_name)
        self._wake_timer()
        return job

    async def disable_job(self, workspace_name: str, job_id: str) -> CronJob | None:
        """Disable a cron job."""
        job = self._find_job(workspace_name, job_id)
        if not job:
            return None
        job.enabled = False
        job.updated_at = time.time()
        self._save(workspace_name)
        return job

    async def run_job_now(self, workspace_name: str, job_id: str) -> bool:
        """Trigger immediate execution of a job."""
        job = self._find_job(workspace_name, job_id)
        if not job:
            return False
        await self._execute_job(workspace_name, job)
        return True

    # --- Timer loop ---

    async def _timer_loop(self) -> None:
        """Main timer loop — sleep until next due job, then execute."""
        while self._running:
            try:
                # Re-scan for new workspaces periodically
                self._scan_workspaces()

                # Check for externally modified stores (mtime-based)
                self._reload_changed_stores()

                # Find soonest due job
                now = time.time()
                next_due = self._find_next_due_time()

                if next_due is not None and next_due <= now:
                    await self._execute_due_jobs()
                    continue  # Immediately re-check

                # Sleep until next due time (capped), interruptible by _wake_event
                if next_due is not None:
                    sleep_for = min(next_due - now, _MAX_TICK_INTERVAL)
                else:
                    sleep_for = _MAX_TICK_INTERVAL
                sleep_for = max(sleep_for, _MIN_TICK_INTERVAL)

                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=sleep_for
                    )
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in cron timer loop")
                await asyncio.sleep(_MAX_TICK_INTERVAL)

    async def _execute_due_jobs(self) -> None:
        """Scan all workspaces and execute due jobs."""
        now = time.time()
        for ws_name, store in list(self._stores.items()):
            jobs_to_delete: list[str] = []
            for job in store.jobs:
                if not job.enabled:
                    continue
                if job.state.running_at:
                    # Check for stuck
                    if (now - job.state.running_at) > _STUCK_TIMEOUT_S:
                        job.state.running_at = None
                        job.state.last_status = "error"
                        job.state.last_error = "stuck (timeout)"
                        job.state.consecutive_errors += 1
                    continue
                if job.state.next_run_at is None or job.state.next_run_at > now:
                    continue

                # Check backoff
                backoff = _backoff_delay(job.state.consecutive_errors)
                if backoff > 0 and job.state.last_run_at:
                    if (now - job.state.last_run_at) < backoff:
                        continue

                await self._execute_job(ws_name, job)

                # Handle delete_after_run
                if job.delete_after_run:
                    jobs_to_delete.append(job.id)

            if jobs_to_delete:
                store.jobs = [j for j in store.jobs if j.id not in jobs_to_delete]

            self._save(ws_name)

    async def _execute_job(
        self,
        workspace_name: str,
        job: CronJob,
        window_id: str | None = None,
    ) -> None:
        """Execute a single cron job by sending message to its tmux window."""
        now = time.time()
        job.state.running_at = now

        try:
            if not window_id:
                window_id = self._resolve_window(workspace_name)
            if not window_id:
                window_id = await self._recreate_window(workspace_name)
            if not window_id:
                raise RuntimeError(f"Cannot find or create window for {workspace_name}")

            # Prefix cron message with creator info using same [Name|uid] format
            send_text = job.message
            if job.creator_user_id:
                creator_name = get_user_display_name(
                    config.users_dir, job.creator_user_id
                )
                if not creator_name:
                    creator_name = str(job.creator_user_id)
                send_text = (
                    f"[{creator_name}|{job.creator_user_id}] [排程任務] {job.message}\n"
                    f"(完成後請 @[{job.creator_user_id}] 告知結果)"
                )

            ok, msg = await session_manager.send_to_window(window_id, send_text)
            if not ok:
                raise RuntimeError(f"send_to_window failed: {msg}")

            duration = time.time() - now
            job.state.running_at = None
            job.state.last_run_at = time.time()
            job.state.last_status = "ok"
            job.state.last_error = ""
            job.state.last_duration_s = round(duration, 1)
            job.state.consecutive_errors = 0
            logger.info(
                "Cron job %s '%s' executed (%.1fs) → %s",
                job.id,
                job.name,
                duration,
                workspace_name,
            )

        except Exception as e:
            duration = time.time() - now
            job.state.running_at = None
            job.state.last_run_at = time.time()
            job.state.last_status = "error"
            job.state.last_error = str(e)[:200]
            job.state.last_duration_s = round(duration, 1)
            job.state.consecutive_errors += 1
            logger.warning(
                "Cron job %s '%s' failed (errors=%d): %s",
                job.id,
                job.name,
                job.state.consecutive_errors,
                e,
            )

        # Compute next run (disable at-jobs after execution)
        if job.schedule.kind == "at":
            job.enabled = False
            job.state.next_run_at = None
        else:
            job.state.next_run_at = compute_next_run(
                job.schedule, time.time(), config.cron_default_tz
            )
            if job.state.next_run_at is None:
                logger.warning("Cron job %s: no next run computed, disabling", job.id)
                job.enabled = False
                job.state.last_error = "invalid schedule"

    # --- Window resolution ---

    def _resolve_window(self, workspace_name: str) -> str | None:
        """Find the tmux window_id for a workspace via thread_bindings."""
        for _user_id, _thread_id, window_id in session_manager.iter_thread_bindings():
            display = session_manager.get_display_name(window_id)
            if display == workspace_name:
                return window_id
        return None

    async def _recreate_window(self, workspace_name: str) -> str | None:
        """Recreate a tmux window for a workspace using stored meta."""
        store = self._stores.get(workspace_name)
        if not store or not store.workspace_meta.user_id:
            logger.warning(
                "Cannot recreate window for %s: no workspace_meta", workspace_name
            )
            return None

        meta = store.workspace_meta
        ws_dir = self._workspace_dirs.get(workspace_name)
        if not ws_dir or not ws_dir.is_dir():
            return None

        # Create tmux window
        success, message, wname, wid = await tmux_manager.create_window(
            str(ws_dir), window_name=workspace_name
        )
        if not success:
            logger.error(
                "Failed to recreate window for %s: %s", workspace_name, message
            )
            return None

        # Wait for hook to register session
        await session_manager.wait_for_session_map_entry(wid)

        # Rebind thread
        session_manager.bind_thread(
            meta.user_id, meta.thread_id, wid, window_name=wname
        )
        if meta.chat_id:
            session_manager.set_group_chat_id(
                meta.user_id, meta.thread_id, meta.chat_id
            )

        logger.info(
            "Recreated window %s (id=%s) for workspace %s",
            wname,
            wid,
            workspace_name,
        )
        return wid

    # --- Internal helpers ---

    def _scan_workspaces(self) -> None:
        """Scan config_dir for workspace directories with cron stores."""
        for ws_dir in config.iter_workspace_dirs():
            ws_name = ws_dir.name.removeprefix("workspace_")
            if ws_name in self._stores:
                continue
            cron_file = ws_dir / "cron" / "jobs.json"
            if not cron_file.is_file():
                continue
            store = load_store(ws_dir)
            self._stores[ws_name] = store
            self._workspace_dirs[ws_name] = ws_dir
            self._mtimes[ws_name] = store_mtime(ws_dir)

    def _reload_changed_stores(self) -> None:
        """Reload stores whose JSON file has been modified externally."""
        for ws_name in list(self._stores.keys()):
            ws_dir = self._workspace_dirs[ws_name]
            current_mtime = store_mtime(ws_dir)
            if current_mtime != self._mtimes.get(ws_name, 0):
                self._stores[ws_name] = load_store(ws_dir)
                self._mtimes[ws_name] = current_mtime
                logger.debug("Reloaded cron store for %s", ws_name)

    def _ensure_store(self, workspace_name: str) -> CronStoreFile:
        """Get or create store for a workspace name."""
        if workspace_name not in self._stores:
            ws_dir = config.workspace_dir_for(workspace_name)
            self._stores[workspace_name] = CronStoreFile()
            self._workspace_dirs[workspace_name] = ws_dir
        return self._stores[workspace_name]

    def _find_job(self, workspace_name: str, job_id: str) -> CronJob | None:
        """Find a job by ID in a workspace."""
        store = self._stores.get(workspace_name)
        if not store:
            return None
        for job in store.jobs:
            if job.id == job_id:
                return job
        return None

    def _find_next_due_time(self) -> float | None:
        """Find the earliest next_run_at across all enabled jobs."""
        earliest: float | None = None
        for store in self._stores.values():
            for job in store.jobs:
                if not job.enabled or job.state.next_run_at is None:
                    continue
                if earliest is None or job.state.next_run_at < earliest:
                    earliest = job.state.next_run_at
        return earliest

    def _save(self, workspace_name: str) -> None:
        """Save a workspace's store and update mtime cache."""
        store = self._stores.get(workspace_name)
        ws_dir = self._workspace_dirs.get(workspace_name)
        if store and ws_dir:
            save_store(ws_dir, store)
            self._mtimes[workspace_name] = store_mtime(ws_dir)

    def _wake_timer(self) -> None:
        """Signal the timer loop to re-evaluate schedule immediately."""
        self._wake_event.set()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_jobs(self) -> int:
        return sum(len(s.jobs) for s in self._stores.values())

    @property
    def workspace_count(self) -> int:
        return len(self._stores)


# Global singleton
cron_service = CronService()
