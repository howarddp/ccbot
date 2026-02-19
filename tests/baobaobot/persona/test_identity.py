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
    shared = tmp_path / "shared"
    ws = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, ws)
    wm.init_shared()
    return shared


class TestParseIdentity:
    def test_default_template(self) -> None:
        content = """# Identity

- **Name**: BaoBao
- **Role**: Personal AI Assistant
- **Emoji**: 🐾
- **Vibe**: warm, dependable, sharp
"""
        identity = parse_identity(content)
        assert identity.name == "BaoBao"
        assert identity.role == "Personal AI Assistant"
        assert identity.emoji == "🐾"
        assert identity.vibe == "warm, dependable, sharp"

    def test_chinese_keys_backward_compat(self) -> None:
        """Existing workspaces with Chinese keys should still parse correctly."""
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

    def test_mixed_keys(self) -> None:
        """Files with a mix of English and Chinese keys should parse correctly."""
        content = """# Identity

- **Name**: TestBot
- **角色**: 超級助理
- **Emoji**: 🤖
- **Vibe**: energetic
"""
        identity = parse_identity(content)
        assert identity.name == "TestBot"
        assert identity.role == "超級助理"
        assert identity.emoji == "🤖"
        assert identity.vibe == "energetic"

    def test_empty_content(self) -> None:
        identity = parse_identity("")
        assert identity == AgentIdentity()

    def test_partial_content(self) -> None:
        content = "- **Name**: TestBot"
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
        updated = update_identity(workspace, name="TestBot")
        assert updated.name == "TestBot"

        # Verify persisted
        identity = read_identity(workspace)
        assert identity.name == "TestBot"

    def test_update_emoji(self, workspace: Path) -> None:
        updated = update_identity(workspace, emoji="🤖")
        assert updated.emoji == "🤖"

    def test_update_multiple(self, workspace: Path) -> None:
        updated = update_identity(workspace, name="TestBot", vibe="lively")
        assert updated.name == "TestBot"
        assert updated.vibe == "lively"
        assert updated.role == "Personal AI Assistant"  # unchanged

    def test_output_uses_english_keys(self, workspace: Path) -> None:
        """update_identity should write English keys to the file."""
        update_identity(workspace, name="TestBot")
        content = (workspace / "IDENTITY.md").read_text()
        assert "**Name**:" in content
        assert "**Role**:" in content
        assert "**Vibe**:" in content
        # Should not contain Chinese keys
        assert "名字" not in content
        assert "角色" not in content
        assert "氛圍" not in content
