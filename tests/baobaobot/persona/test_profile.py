"""Tests for persona/profile.py — USER.md parsing and updating."""

from pathlib import Path

import pytest

from baobaobot.persona.profile import (
    UserProfile,
    get_user_display_name,
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

- **Name**: Howard
- **Nickname**: Boss
- **Timezone**: Asia/Taipei
- **Language**: English
- **Notes**: likes coffee

## Context
Working on BaoBaoClaude
"""
        profile = parse_profile(content)
        assert profile.name == "Howard"
        assert profile.nickname == "Boss"
        assert profile.timezone == "Asia/Taipei"
        assert profile.language == "English"
        assert profile.notes == "likes coffee"
        assert "BaoBaoClaude" in profile.context

    def test_chinese_keys_backward_compat(self) -> None:
        """Existing workspaces with Chinese keys should still parse correctly."""
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

    def test_mixed_keys(self) -> None:
        """Files with a mix of English and Chinese keys should parse correctly."""
        content = """# User

- **Name**: Howard
- **稱呼**: 老闆
- **Timezone**: Asia/Taipei
- **語言偏好**: 繁體中文
- **Notes**: likes coffee
"""
        profile = parse_profile(content)
        assert profile.name == "Howard"
        assert profile.nickname == "老闆"
        assert profile.timezone == "Asia/Taipei"
        assert profile.language == "繁體中文"
        assert profile.notes == "likes coffee"

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
        updated = update_profile(workspace, name="Howard", nickname="Boss")
        assert updated.name == "Howard"
        assert updated.nickname == "Boss"
        assert updated.timezone == "Asia/Taipei"  # unchanged

    def test_output_uses_english_keys(self, workspace: Path) -> None:
        """update_profile should write English keys to the file."""
        update_profile(workspace, name="Howard")
        content = (workspace / "USER.md").read_text()
        assert "**Name**:" in content
        assert "**Timezone**:" in content
        assert "**Language**:" in content
        # Should not contain Chinese keys
        assert "名字" not in content
        assert "時區" not in content
        assert "語言偏好" not in content


class TestGetUserDisplayName:
    def test_returns_none_for_not_set_english(self, tmp_path: Path) -> None:
        """'(not set)' sentinel should return None."""
        users_dir = tmp_path / "users"
        users_dir.mkdir()
        (users_dir / "123.md").write_text("# User\n\n- **Name**: (not set)\n")
        assert get_user_display_name(users_dir, 123) is None

    def test_returns_none_for_not_set_chinese(self, tmp_path: Path) -> None:
        """'（待設定）' sentinel should also return None (backward compat)."""
        users_dir = tmp_path / "users"
        users_dir.mkdir()
        (users_dir / "456.md").write_text("# User\n\n- **名字**: （待設定）\n")
        assert get_user_display_name(users_dir, 456) is None

    def test_returns_name_when_set(self, tmp_path: Path) -> None:
        users_dir = tmp_path / "users"
        users_dir.mkdir()
        (users_dir / "789.md").write_text("# User\n\n- **Name**: Alice\n")
        assert get_user_display_name(users_dir, 789) == "Alice"
