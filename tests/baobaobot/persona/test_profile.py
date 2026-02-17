"""Tests for persona/profile.py — USER.md parsing and updating."""

from pathlib import Path

import pytest

from baobaobot.persona.profile import (
    UserProfile,
    parse_profile,
    read_profile,
    update_profile,
)
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    shared = tmp_path / "shared"
    ws = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, ws)
    wm.init_shared()
    return shared


class TestParseProfile:
    def test_default_template(self) -> None:
        content = """# User

- **名字**: Howard
- **稱呼**: 老闆
- **時區**: Asia/Taipei
- **語言偏好**: 繁體中文
- **備註**: likes coffee

## Context
Working on BaoBaoClaude
"""
        profile = parse_profile(content)
        assert profile.name == "Howard"
        assert profile.nickname == "老闆"
        assert profile.timezone == "Asia/Taipei"
        assert profile.language == "繁體中文"
        assert profile.notes == "likes coffee"
        assert "BaoBaoClaude" in profile.context

    def test_empty_content(self) -> None:
        profile = parse_profile("")
        assert profile == UserProfile()


class TestReadProfile:
    def test_from_workspace(self, workspace: Path) -> None:
        profile = read_profile(workspace)
        assert profile.timezone == "Asia/Taipei"

    def test_missing_file(self, tmp_path: Path) -> None:
        profile = read_profile(tmp_path)
        assert profile == UserProfile()


class TestUpdateProfile:
    def test_update_name(self, workspace: Path) -> None:
        updated = update_profile(workspace, name="Howard")
        assert updated.name == "Howard"

        # Verify persisted
        profile = read_profile(workspace)
        assert profile.name == "Howard"

    def test_update_timezone(self, workspace: Path) -> None:
        updated = update_profile(workspace, timezone="US/Pacific")
        assert updated.timezone == "US/Pacific"

    def test_update_multiple(self, workspace: Path) -> None:
        updated = update_profile(workspace, name="Howard", nickname="老闆")
        assert updated.name == "Howard"
        assert updated.nickname == "老闆"
        assert updated.timezone == "Asia/Taipei"  # unchanged
