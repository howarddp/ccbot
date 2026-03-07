"""Tests for workspace_config module."""

from pathlib import Path

from baobaobot.workspace_config import (
    ensure_defaults,
    get_agent_type,
    get_verbosity,
    set_agent_type,
    set_verbosity,
    _read_toml,
    _serialize_toml,
)


class TestWorkspaceConfig:
    def test_get_agent_type_missing_file(self, tmp_path: Path):
        assert get_agent_type(tmp_path) == ""

    def test_set_and_get_agent_type(self, tmp_path: Path):
        set_agent_type(tmp_path, "gemini")
        assert get_agent_type(tmp_path) == "gemini"
        # File should exist
        assert (tmp_path / "workspace.toml").is_file()

    def test_set_agent_type_overwrite(self, tmp_path: Path):
        set_agent_type(tmp_path, "gemini")
        set_agent_type(tmp_path, "claude")
        assert get_agent_type(tmp_path) == "claude"

    def test_get_verbosity_missing_file(self, tmp_path: Path):
        assert get_verbosity(tmp_path, 123) == ""

    def test_set_and_get_verbosity(self, tmp_path: Path):
        set_verbosity(tmp_path, 123, "quiet")
        assert get_verbosity(tmp_path, 123) == "quiet"

    def test_verbosity_per_user(self, tmp_path: Path):
        set_verbosity(tmp_path, 111, "quiet")
        set_verbosity(tmp_path, 222, "verbose")
        assert get_verbosity(tmp_path, 111) == "quiet"
        assert get_verbosity(tmp_path, 222) == "verbose"

    def test_combined_settings(self, tmp_path: Path):
        set_agent_type(tmp_path, "gemini")
        set_verbosity(tmp_path, 123, "quiet")
        # Both should coexist
        assert get_agent_type(tmp_path) == "gemini"
        assert get_verbosity(tmp_path, 123) == "quiet"

    def test_serialize_roundtrip(self, tmp_path: Path):
        set_agent_type(tmp_path, "gemini")
        set_verbosity(tmp_path, 7022938281, "quiet")
        set_verbosity(tmp_path, 8179448227, "verbose")
        # Read back
        data = _read_toml(tmp_path)
        assert data["workspace"]["agent_type"] == "gemini"
        assert data["users"]["7022938281"]["verbosity"] == "quiet"
        assert data["users"]["8179448227"]["verbosity"] == "verbose"

    def test_serialize_format(self):
        data = {
            "workspace": {"agent_type": "gemini"},
            "users": {"123": {"verbosity": "quiet"}},
        }
        result = _serialize_toml(data)
        assert '[workspace]' in result
        assert 'agent_type = "gemini"' in result
        assert '[users.123]' in result
        assert 'verbosity = "quiet"' in result

    def test_invalid_verbosity_raises(self, tmp_path: Path):
        import pytest
        with pytest.raises(ValueError):
            set_verbosity(tmp_path, 123, "invalid")

    def test_ensure_defaults_creates_file(self, tmp_path: Path):
        ensure_defaults(tmp_path, "claude", 123)
        assert (tmp_path / "workspace.toml").is_file()
        assert get_agent_type(tmp_path) == "claude"
        assert get_verbosity(tmp_path, 123) == "normal"

    def test_ensure_defaults_no_overwrite(self, tmp_path: Path):
        set_agent_type(tmp_path, "gemini")
        set_verbosity(tmp_path, 123, "quiet")
        ensure_defaults(tmp_path, "claude", 123)
        # Should not overwrite existing values
        assert get_agent_type(tmp_path) == "gemini"
        assert get_verbosity(tmp_path, 123) == "quiet"

    def test_ensure_defaults_fills_missing_user(self, tmp_path: Path):
        set_agent_type(tmp_path, "claude")
        ensure_defaults(tmp_path, "claude", 456)
        # Existing agent_type preserved, new user gets default
        assert get_agent_type(tmp_path) == "claude"
        assert get_verbosity(tmp_path, 456) == "normal"
