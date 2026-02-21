"""Tests for SessionManager pure dict operations."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from baobaobot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch, tmp_path: Path) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager(
        state_file=tmp_path / "state.json",
        session_map_file=tmp_path / "session_map.json",
        tmux_session_name="test",
        claude_projects_path=tmp_path / "projects",
        tmux_manager=MagicMock(),
    )


class TestThreadBindings:
    def test_bind_and_get(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        assert mgr.get_window_for_thread(100, 1) == "@1"

    def test_bind_unbind_get_returns_none(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.unbind_thread(100, 1)
        assert mgr.get_window_for_thread(100, 1) is None

    def test_unbind_nonexistent_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.unbind_thread(100, 999) is None

    def test_get_thread_for_window(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 42, "@5")
        assert mgr.get_thread_for_window(100, "@5") == 42

    def test_iter_thread_bindings(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.bind_thread(100, 2, "@2")
        mgr.bind_thread(200, 3, "@3")
        result = set(mgr.iter_thread_bindings())
        assert result == {(100, 1, "@1"), (100, 2, "@2"), (200, 3, "@3")}


class TestResolveChatId:
    def test_with_stored_group_id(self, mgr: SessionManager) -> None:
        mgr.set_group_chat_id(100, 1, -999)
        assert mgr.resolve_chat_id(100, 1) == -999

    def test_without_group_id_falls_back(self, mgr: SessionManager) -> None:
        assert mgr.resolve_chat_id(100, 1) == 100

    def test_none_thread_id_falls_back(self, mgr: SessionManager) -> None:
        mgr.set_group_chat_id(100, 1, -999)
        assert mgr.resolve_chat_id(100) == 100


class TestWindowState:
    def test_get_creates_new(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@0")
        assert state.session_id == ""
        assert state.cwd == ""

    def test_get_returns_existing(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@1")
        state.session_id = "abc"
        assert mgr.get_window_state("@1").session_id == "abc"

    def test_clear_window_session(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@1")
        state.session_id = "abc"
        mgr.clear_window_session("@1")
        assert mgr.get_window_state("@1").session_id == ""


class TestResolveWindowForThread:
    def test_none_thread_id_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.resolve_window_for_thread(100, None) is None

    def test_unbound_thread_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.resolve_window_for_thread(100, 42) is None

    def test_bound_thread_returns_window(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 42, "@3")
        assert mgr.resolve_window_for_thread(100, 42) == "@3"


class TestDisplayNames:
    def test_get_display_name_fallback(self, mgr: SessionManager) -> None:
        """get_display_name returns window_id when no display name is set."""
        assert mgr.get_display_name("@99") == "@99"

    def test_set_and_get_display_name(self, mgr: SessionManager) -> None:
        mgr.set_display_name("@1", "myproject")
        assert mgr.get_display_name("@1") == "myproject"

    def test_set_display_name_update(self, mgr: SessionManager) -> None:
        mgr.set_display_name("@1", "old-name")
        mgr.set_display_name("@1", "new-name")
        assert mgr.get_display_name("@1") == "new-name"

    def test_bind_thread_sets_display_name(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", window_name="proj")
        assert mgr.get_display_name("@1") == "proj"

    def test_bind_thread_without_name_no_display(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        # No display name set, fallback to window_id
        assert mgr.get_display_name("@1") == "@1"


class TestTopicNames:
    def test_get_returns_none_by_default(self, mgr: SessionManager) -> None:
        assert mgr.get_topic_name(42) is None

    def test_set_and_get(self, mgr: SessionManager) -> None:
        mgr.set_topic_name(42, "my-project")
        assert mgr.get_topic_name(42) == "my-project"

    def test_update(self, mgr: SessionManager) -> None:
        mgr.set_topic_name(42, "old-name")
        mgr.set_topic_name(42, "new-name")
        assert mgr.get_topic_name(42) == "new-name"


class TestVerbosity:
    def test_default_is_normal(self, mgr: SessionManager) -> None:
        assert mgr.get_verbosity(42, 0) == "normal"

    def test_set_and_get(self, mgr: SessionManager) -> None:
        mgr.set_verbosity(42, 100, "quiet")
        assert mgr.get_verbosity(42, 100) == "quiet"

    def test_set_verbose(self, mgr: SessionManager) -> None:
        mgr.set_verbosity(42, 100, "verbose")
        assert mgr.get_verbosity(42, 100) == "verbose"

    def test_invalid_level_raises(self, mgr: SessionManager) -> None:
        with pytest.raises(ValueError, match="Invalid verbosity"):
            mgr.set_verbosity(42, 100, "invalid")

    def test_different_users(self, mgr: SessionManager) -> None:
        mgr.set_verbosity(1, 100, "quiet")
        mgr.set_verbosity(2, 200, "verbose")
        assert mgr.get_verbosity(1, 100) == "quiet"
        assert mgr.get_verbosity(2, 200) == "verbose"
        assert mgr.get_verbosity(3, 0) == "normal"  # default

    def test_different_threads_same_user(self, mgr: SessionManager) -> None:
        mgr.set_verbosity(42, 100, "quiet")
        mgr.set_verbosity(42, 200, "verbose")
        assert mgr.get_verbosity(42, 100) == "quiet"
        assert mgr.get_verbosity(42, 200) == "verbose"
        assert mgr.get_verbosity(42, 300) == "normal"  # unset thread


class TestIsWindowId:
    def test_valid_ids(self, mgr: SessionManager) -> None:
        assert mgr._is_window_id("@0") is True
        assert mgr._is_window_id("@12") is True
        assert mgr._is_window_id("@999") is True

    def test_invalid_ids(self, mgr: SessionManager) -> None:
        assert mgr._is_window_id("myproject") is False
        assert mgr._is_window_id("@") is False
        assert mgr._is_window_id("") is False
        assert mgr._is_window_id("@abc") is False
