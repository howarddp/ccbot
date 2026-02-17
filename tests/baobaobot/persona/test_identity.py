"""Tests for persona/identity.py — IDENTITY.md parsing and updating."""

from pathlib import Path

import pytest

from baobaobot.persona.identity import (
    AgentIdentity,
    parse_identity,
    read_identity,
    update_identity,
)
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    wm = WorkspaceManager(tmp_path / "workspace")
    wm.init()
    return wm.workspace_dir


class TestParseIdentity:
    def test_default_template(self) -> None:
        content = """# Identity

- **名字**: BaoBao
- **角色**: 個人 AI 助理
- **Emoji**: 🐾
- **氛圍**: 溫暖、可靠、聰明
"""
        identity = parse_identity(content)
        assert identity.name == "BaoBao"
        assert identity.role == "個人 AI 助理"
        assert identity.emoji == "🐾"
        assert identity.vibe == "溫暖、可靠、聰明"

    def test_empty_content(self) -> None:
        identity = parse_identity("")
        assert identity == AgentIdentity()

    def test_partial_content(self) -> None:
        content = "- **名字**: TestBot"
        identity = parse_identity(content)
        assert identity.name == "TestBot"
        assert identity.emoji == "🐾"  # default


class TestReadIdentity:
    def test_from_workspace(self, workspace: Path) -> None:
        identity = read_identity(workspace)
        assert identity.name == "BaoBao"

    def test_missing_file(self, tmp_path: Path) -> None:
        identity = read_identity(tmp_path)
        assert identity == AgentIdentity()


class TestUpdateIdentity:
    def test_update_name(self, workspace: Path) -> None:
        updated = update_identity(workspace, name="小寶")
        assert updated.name == "小寶"

        # Verify persisted
        identity = read_identity(workspace)
        assert identity.name == "小寶"

    def test_update_emoji(self, workspace: Path) -> None:
        updated = update_identity(workspace, emoji="🤖")
        assert updated.emoji == "🤖"

    def test_update_multiple(self, workspace: Path) -> None:
        updated = update_identity(workspace, name="小寶", vibe="活潑")
        assert updated.name == "小寶"
        assert updated.vibe == "活潑"
        assert updated.role == "個人 AI 助理"  # unchanged
