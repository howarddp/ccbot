"""Tests for verbosity persistence in SessionManager state file."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from baobaobot.session import SessionManager


def _make_mgr(tmp_path: Path) -> SessionManager:
    """Create a SessionManager with real save/load (no mocking)."""
    return SessionManager(
        state_file=tmp_path / "state.json",
        session_map_file=tmp_path / "session_map.json",
        tmux_session_name="test",
        claude_projects_path=tmp_path / "projects",
        tmux_manager=MagicMock(),
    )


class TestVerbosityPersistence:
    def test_roundtrip(self, tmp_path: Path):
        """Verbosity survives save/load cycle."""
        mgr1 = _make_mgr(tmp_path)
        mgr1.set_verbosity(42, "quiet")
        mgr1.set_verbosity(99, "verbose")

        # Create a new manager that loads from the same state file
        mgr2 = _make_mgr(tmp_path)
        assert mgr2.get_verbosity(42) == "quiet"
        assert mgr2.get_verbosity(99) == "verbose"
        assert mgr2.get_verbosity(1) == "normal"  # default

    def test_serialized_in_state_json(self, tmp_path: Path):
        """Verify the JSON structure in state.json."""
        mgr = _make_mgr(tmp_path)
        mgr.set_verbosity(42, "quiet")

        state = json.loads((tmp_path / "state.json").read_text())
        assert "user_verbosity" in state
        assert state["user_verbosity"]["42"] == "quiet"

    def test_missing_key_loads_empty(self, tmp_path: Path):
        """Old state.json without user_verbosity loads cleanly."""
        (tmp_path / "state.json").write_text("{}")
        mgr = _make_mgr(tmp_path)
        assert mgr.get_verbosity(42) == "normal"
