"""Tests for group mode — binding, state persistence, and session manager integration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from baobaobot.session import SessionManager


def _make_session_manager(tmp_path: Path) -> SessionManager:
    """Create a SessionManager with temp files for testing."""
    tm = MagicMock()
    tm.list_windows = AsyncMock(return_value=[])
    return SessionManager(
        state_file=tmp_path / "state.json",
        session_map_file=tmp_path / "session_map.json",
        tmux_session_name="test",
        claude_projects_path=tmp_path / "projects",
        tmux_manager=tm,
    )


class TestGroupBindings:
    def test_bind_group(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5", "Dev Team")

        assert sm.get_window_for_group(-1001) == "@5"
        assert sm.get_group_title(-1001) == "Dev Team"
        assert sm.window_display_names.get("@5") == "Dev Team"

    def test_unbind_group(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5", "Dev Team")

        wid = sm.unbind_group(-1001)
        assert wid == "@5"
        assert sm.get_window_for_group(-1001) is None
        assert sm.get_group_title(-1001) is None

    def test_unbind_nonexistent(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        assert sm.unbind_group(-9999) is None

    def test_iter_group_bindings(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5", "Team A")
        sm.bind_group(-1002, "@6", "Team B")

        bindings = list(sm.iter_group_bindings())
        assert len(bindings) == 2
        chat_ids = {cid for cid, _ in bindings}
        assert chat_ids == {-1001, -1002}

    def test_set_group_title(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5")
        sm.set_group_title(-1001, "New Title")

        assert sm.get_group_title(-1001) == "New Title"
        assert sm.window_display_names.get("@5") == "New Title"


class TestGroupStatePersistence:
    def test_save_and_load(self, tmp_path):
        sm1 = _make_session_manager(tmp_path)
        sm1.bind_group(-1001, "@5", "Dev Team")
        sm1.bind_group(-1002, "@6", "Design Team")

        # Create a new SessionManager that loads from the same state file
        sm2 = _make_session_manager(tmp_path)

        assert sm2.get_window_for_group(-1001) == "@5"
        assert sm2.get_window_for_group(-1002) == "@6"
        assert sm2.get_group_title(-1001) == "Dev Team"
        assert sm2.get_group_title(-1002) == "Design Team"

    def test_state_file_format(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5", "Test Group")

        state = json.loads((tmp_path / "state.json").read_text())
        assert "group_bindings" in state
        assert state["group_bindings"]["-1001"] == "@5"
        assert "group_titles" in state
        assert state["group_titles"]["-1001"] == "Test Group"


class TestFindGroupsForSession:
    @pytest.mark.asyncio
    async def test_find_groups(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5", "Team A")
        sm.bind_group(-1002, "@6", "Team B")

        # Mock resolve_session_for_window to return matching session
        mock_session = MagicMock()
        mock_session.session_id = "session-abc"
        mock_session.file_path = "/tmp/test.jsonl"

        async def mock_resolve(wid):
            if wid == "@5":
                return mock_session
            return None

        sm.resolve_session_for_window = mock_resolve

        result = await sm.find_groups_for_session("session-abc")
        assert len(result) == 1
        assert result[0] == (-1001, "@5")

    @pytest.mark.asyncio
    async def test_find_groups_no_match(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        sm.bind_group(-1001, "@5", "Team A")

        sm.resolve_session_for_window = AsyncMock(return_value=None)

        result = await sm.find_groups_for_session("nonexistent")
        assert result == []


class TestSettingsMode:
    def test_forum_mode_default(self, tmp_path):
        """Settings with no mode specified defaults to 'forum'."""
        toml_content = """
[global]
allowed_users = [123]
claude_command = "claude"

[[agents]]
name = "test"
bot_token_env = "TEST_TOKEN"
"""
        import os
        import tomllib

        from baobaobot.settings import _build_agent_config

        os.environ["TEST_TOKEN"] = "fake-token"
        try:
            raw = tomllib.loads(toml_content)
            cfg = _build_agent_config(
                tmp_path,
                raw.get("global", {}),
                raw["agents"][0],
            )
            assert cfg.mode == "forum"
        finally:
            os.environ.pop("TEST_TOKEN", None)

    def test_group_mode(self, tmp_path):
        """Settings with mode='group' creates correct config."""
        import os
        import tomllib

        from baobaobot.settings import _build_agent_config

        toml_content = """
[global]
allowed_users = [123]

[[agents]]
name = "test"
bot_token_env = "TEST_TOKEN"
mode = "group"
"""
        os.environ["TEST_TOKEN"] = "fake-token"
        try:
            raw = tomllib.loads(toml_content)
            cfg = _build_agent_config(
                tmp_path,
                raw.get("global", {}),
                raw["agents"][0],
            )
            assert cfg.mode == "group"
        finally:
            os.environ.pop("TEST_TOKEN", None)

    def test_create_router_from_config(self, tmp_path):
        """AgentConfig mode creates the correct router."""
        from baobaobot.routers import create_router
        from baobaobot.routers.forum import ForumRouter
        from baobaobot.routers.group import GroupRouter

        assert isinstance(create_router("forum"), ForumRouter)
        assert isinstance(create_router("group"), GroupRouter)
