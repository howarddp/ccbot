"""Tests for SystemScheduler heartbeat DB logic."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baobaobot.settings import SchedulerConfig
from baobaobot.system_scheduler import (
    SystemScheduler,
    _connect,
    _get_meta,
    _set_meta,
    _META_KEY_HEARTBEAT_CONTENT_HASH,
    _META_KEY_HEARTBEAT_ENABLED,
    _META_KEY_HEARTBEAT_LAST_TIME,
    _META_KEY_HEARTBEAT_NEXT_RUN,
)

_CFG = SchedulerConfig()


@pytest.fixture
def ws_dir(tmp_path: Path) -> Path:
    """Create a temporary workspace directory with memory.db."""
    ws = tmp_path / "workspace_test"
    ws.mkdir()
    # Initialize DB with cron_meta table
    conn = _connect(ws)
    conn.close()
    return ws


@pytest.fixture
def scheduler() -> SystemScheduler:
    """Create a SystemScheduler with mocked dependencies."""
    session_mgr = MagicMock()
    session_mgr.get_last_interaction_time.return_value = None
    tmux_mgr = MagicMock()
    sched = SystemScheduler(
        session_manager=session_mgr,
        tmux_manager=tmux_mgr,
        agent_name="test",
        timezone="UTC",
        iter_workspace_dirs=lambda: [],
        on_notify=AsyncMock(),
        scheduler_config=SchedulerConfig(),
    )
    return sched


class TestGetHeartbeatState:
    """Test _get_heartbeat_state reads all keys in one connection."""

    def test_defaults_when_empty(self, scheduler: SystemScheduler, ws_dir: Path):
        """All defaults returned when no meta keys exist."""
        state = scheduler._get_heartbeat_state(ws_dir)
        assert state["enabled"] == "1"
        assert state["next_run"] == "0"
        assert state["last_time"] == "0"
        assert state["content_hash"] == ""

    def test_reads_written_values(self, scheduler: SystemScheduler, ws_dir: Path):
        """Values written to DB are read back correctly."""
        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_ENABLED, "0")
        _set_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, "1700000000")
        _set_meta(conn, _META_KEY_HEARTBEAT_LAST_TIME, "1699999000")
        _set_meta(conn, _META_KEY_HEARTBEAT_CONTENT_HASH, "abc123")
        conn.commit()
        conn.close()

        state = scheduler._get_heartbeat_state(ws_dir)
        assert state["enabled"] == "0"
        assert state["next_run"] == "1700000000"
        assert state["last_time"] == "1699999000"
        assert state["content_hash"] == "abc123"

    def test_partial_values(self, scheduler: SystemScheduler, ws_dir: Path):
        """Only some keys set — others get defaults."""
        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_ENABLED, "0")
        conn.commit()
        conn.close()

        state = scheduler._get_heartbeat_state(ws_dir)
        assert state["enabled"] == "0"
        assert state["next_run"] == "0"  # default
        assert state["last_time"] == "0"  # default
        assert state["content_hash"] == ""  # default

    def test_nonexistent_db(self, scheduler: SystemScheduler, tmp_path: Path):
        """Gracefully returns defaults when DB doesn't exist yet."""
        bad_dir = tmp_path / "workspace_nope"
        bad_dir.mkdir()
        state = scheduler._get_heartbeat_state(bad_dir)
        # Should still return defaults (DB auto-created by _connect)
        assert state["enabled"] == "1"


class TestCheckSingleHeartbeat:
    """Test _check_single_heartbeat decision logic."""

    @pytest.mark.asyncio
    async def test_skip_when_disabled(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """Heartbeat disabled → skip, no tmux send."""
        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_ENABLED, "0")
        conn.commit()
        conn.close()

        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)

        await scheduler._check_single_heartbeat("test", ws_dir, time.time())
        scheduler._tmux_manager.send_keys.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_not_due(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """next_run in the future → skip."""
        future = time.time() + 99999
        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, str(future))
        conn.commit()
        conn.close()

        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)

        await scheduler._check_single_heartbeat("test", ws_dir, time.time())
        scheduler._tmux_manager.send_keys.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_content_and_no_todos(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """HEARTBEAT.md empty + no open TODOs → skip and reschedule."""
        # No HEARTBEAT.md file, no todos table
        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)

        now = time.time()
        await scheduler._check_single_heartbeat("test", ws_dir, now)
        scheduler._tmux_manager.send_keys.assert_not_called()

        # Verify next_run was rescheduled
        conn = _connect(ws_dir)
        next_run = float(_get_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, "0"))
        conn.close()
        assert next_run >= now + _CFG.heartbeat_interval - 1

    @pytest.mark.asyncio
    async def test_send_when_only_todos(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """No HEARTBEAT.md but has open TODOs → should send."""
        # Create todos table with an open item
        conn = sqlite3.connect(str(ws_dir / "memory.db"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS todos ("
            "id TEXT PRIMARY KEY, type TEXT, title TEXT, content TEXT, "
            "created_by TEXT, created_by_id TEXT, created_at TEXT, "
            "start_date TEXT, deadline TEXT, location TEXT, "
            "status TEXT DEFAULT 'open', done_at TEXT, attachments TEXT DEFAULT '[]')"
        )
        conn.execute(
            "INSERT INTO todos (id, type, title, content, created_by, created_by_id, created_at, status) "
            "VALUES ('T20260301-1', 'task', 'Test task', '', 'Howard', '123', '2026-03-01', 'open')"
        )
        conn.commit()
        conn.close()

        now = time.time()
        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)
        scheduler._resolve_window = MagicMock(return_value="@0")
        scheduler._get_jsonl_path = MagicMock(return_value=None)
        scheduler._is_summary_due = MagicMock(return_value=False)

        await scheduler._check_single_heartbeat("test", ws_dir, now)
        scheduler._tmux_manager.send_keys.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_dedup_same_hash_recent(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """Same content hash + sent < 2h ago → dedup skip."""
        hb_file = ws_dir / "HEARTBEAT.md"
        hb_file.write_text("- Track deployment status\n", encoding="utf-8")

        # Hash now combines heartbeat content + todo_hash (empty here)
        content_hash = scheduler._compute_content_hash("- Track deployment status")
        now = time.time()

        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_CONTENT_HASH, content_hash)
        _set_meta(conn, _META_KEY_HEARTBEAT_LAST_TIME, str(now - 600))  # 10 min ago
        _set_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, "0")  # due
        conn.commit()
        conn.close()

        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)

        await scheduler._check_single_heartbeat("test", ws_dir, now)
        scheduler._tmux_manager.send_keys.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_when_hash_changed(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """Different content hash → should send heartbeat."""
        hb_file = ws_dir / "HEARTBEAT.md"
        hb_file.write_text("- New item to track\n", encoding="utf-8")

        now = time.time()
        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_CONTENT_HASH, "old_hash")
        _set_meta(conn, _META_KEY_HEARTBEAT_LAST_TIME, str(now - 600))
        _set_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, "0")
        conn.commit()
        conn.close()

        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)

        # Mock _resolve_window to return a window ID
        scheduler._resolve_window = MagicMock(return_value="@0")
        # Mock _get_jsonl_path to return None (no idle check needed)
        scheduler._get_jsonl_path = MagicMock(return_value=None)
        # Mock _is_summary_due to avoid collision check
        scheduler._is_summary_due = MagicMock(return_value=False)

        await scheduler._check_single_heartbeat("test", ws_dir, now)
        scheduler._tmux_manager.send_keys.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_when_dedup_expired(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """Same hash but last send > 2h ago → should send."""
        hb_file = ws_dir / "HEARTBEAT.md"
        hb_file.write_text("- Track deployment status\n", encoding="utf-8")

        content_hash = scheduler._compute_content_hash("- Track deployment status")
        now = time.time()

        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_CONTENT_HASH, content_hash)
        _set_meta(
            conn, _META_KEY_HEARTBEAT_LAST_TIME,
            str(now - _CFG.heartbeat_dedup - 100),  # > 2h ago
        )
        _set_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, "0")
        conn.commit()
        conn.close()

        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)
        scheduler._resolve_window = MagicMock(return_value="@0")
        scheduler._get_jsonl_path = MagicMock(return_value=None)
        scheduler._is_summary_due = MagicMock(return_value=False)

        await scheduler._check_single_heartbeat("test", ws_dir, now)
        scheduler._tmux_manager.send_keys.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_when_session_not_idle(
        self, scheduler: SystemScheduler, ws_dir: Path
    ):
        """Session active (recent interaction) → skip."""
        hb_file = ws_dir / "HEARTBEAT.md"
        hb_file.write_text("- Active item\n", encoding="utf-8")

        now = time.time()
        conn = _connect(ws_dir)
        _set_meta(conn, _META_KEY_HEARTBEAT_NEXT_RUN, "0")
        conn.commit()
        conn.close()

        # Session had interaction 1 minute ago (< 5 min idle threshold)
        scheduler._session_manager.get_last_interaction_time.return_value = now - 60

        scheduler._tmux_manager = MagicMock()
        scheduler._tmux_manager.send_keys = AsyncMock(return_value=True)
        scheduler._resolve_window = MagicMock(return_value="@0")
        scheduler._get_jsonl_path = MagicMock(return_value=None)

        await scheduler._check_single_heartbeat("test", ws_dir, now)
        scheduler._tmux_manager.send_keys.assert_not_called()
