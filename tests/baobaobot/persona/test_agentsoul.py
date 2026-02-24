"""Tests for persona/agentsoul.py — AGENTSOUL.md read/write and identity parsing."""

from pathlib import Path

import pytest

from baobaobot.persona.agentsoul import (
    AgentIdentity,
    parse_identity,
    read_agentsoul,
    read_agentsoul_with_source,
    read_identity,
    resolve_agentsoul_path,
    update_identity,
    write_agentsoul,
)
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def shared_dir(tmp_path: Path) -> Path:
    shared = tmp_path / "shared"
    ws = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, ws)
    wm.init_shared()
    return shared


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace_local"
    ws.mkdir()
    return ws


def _local_agentsoul(workspace_dir: Path) -> Path:
    """Return the .persona/AGENTSOUL.md path, creating .persona/ if needed."""
    persona = workspace_dir / ".persona"
    persona.mkdir(exist_ok=True)
    return persona / "AGENTSOUL.md"


class TestReadAgentsoul:
    def test_from_shared_dir(self, shared_dir: Path) -> None:
        content = read_agentsoul(shared_dir)
        assert "Agent Soul" in content

    def test_missing_file(self, tmp_path: Path) -> None:
        content = read_agentsoul(tmp_path)
        assert content == ""


class TestWriteAgentsoul:
    def test_write_and_read(self, shared_dir: Path) -> None:
        write_agentsoul(shared_dir, "# Agent Soul\n\nNew personality")
        content = read_agentsoul(shared_dir)
        assert "New personality" in content

    def test_overwrites(self, shared_dir: Path) -> None:
        write_agentsoul(shared_dir, "First")
        write_agentsoul(shared_dir, "Second")
        assert read_agentsoul(shared_dir) == "Second"


class TestParseIdentity:
    def test_default_template(self) -> None:
        content = """# Agent Soul

## Identity
- **Name**: BaoBao
- **Role**: Personal AI Assistant
- **Emoji**: 🐾
- **Vibe**: warm, dependable, sharp

## Personality
- Some personality trait
"""
        identity = parse_identity(content)
        assert identity.name == "BaoBao"
        assert identity.role == "Personal AI Assistant"
        assert identity.emoji == "🐾"
        assert identity.vibe == "warm, dependable, sharp"

    def test_chinese_keys_backward_compat(self) -> None:
        """Existing workspaces with Chinese keys should still parse correctly."""
        content = """# Agent Soul

## Identity
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
        content = """## Identity
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
    def test_from_shared_dir(self, shared_dir: Path) -> None:
        identity = read_identity(shared_dir)
        assert identity.name == "BaoBao"

    def test_missing_file(self, tmp_path: Path) -> None:
        identity = read_identity(tmp_path)
        assert identity == AgentIdentity()


class TestUpdateIdentity:
    def test_update_name(self, shared_dir: Path) -> None:
        updated = update_identity(shared_dir, name="TestBot")
        assert updated.name == "TestBot"

        # Verify persisted
        identity = read_identity(shared_dir)
        assert identity.name == "TestBot"

    def test_update_emoji(self, shared_dir: Path) -> None:
        updated = update_identity(shared_dir, emoji="🤖")
        assert updated.emoji == "🤖"

    def test_update_multiple(self, shared_dir: Path) -> None:
        updated = update_identity(shared_dir, name="TestBot", vibe="lively")
        assert updated.name == "TestBot"
        assert updated.vibe == "lively"
        assert updated.role == "Personal AI Assistant"  # unchanged

    def test_output_uses_english_keys(self, shared_dir: Path) -> None:
        """update_identity should write English keys to the file."""
        update_identity(shared_dir, name="TestBot")
        content = (shared_dir / "AGENTSOUL.md").read_text()
        assert "**Name**:" in content
        assert "**Role**:" in content
        assert "**Vibe**:" in content
        # Should not contain Chinese keys
        assert "名字" not in content
        assert "角色" not in content
        assert "氛圍" not in content

    def test_preserves_soul_sections(self, shared_dir: Path) -> None:
        """update_identity should not destroy Personality/Tone/Boundaries sections."""
        content = read_agentsoul(shared_dir)
        assert "Personality" in content  # sanity check

        update_identity(shared_dir, name="TestBot")

        updated_content = read_agentsoul(shared_dir)
        assert "Personality" in updated_content
        assert "Tone" in updated_content
        assert "Boundaries" in updated_content
        assert "TestBot" in updated_content

    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        """update_identity should create AGENTSOUL.md if it doesn't exist."""
        updated = update_identity(tmp_path, name="NewBot")
        assert updated.name == "NewBot"
        assert (tmp_path / "AGENTSOUL.md").is_file()


class TestResolveAgentsoulPath:
    def test_falls_back_to_shared(self, shared_dir: Path, workspace_dir: Path) -> None:
        path, is_local = resolve_agentsoul_path(shared_dir, workspace_dir)
        assert path == shared_dir / "AGENTSOUL.md"
        assert is_local is False

    def test_prefers_local(self, shared_dir: Path, workspace_dir: Path) -> None:
        _local_agentsoul(workspace_dir).write_text("# Local Soul\n")
        path, is_local = resolve_agentsoul_path(shared_dir, workspace_dir)
        assert path == workspace_dir / ".persona" / "AGENTSOUL.md"
        assert is_local is True

    def test_no_workspace_dir(self, shared_dir: Path) -> None:
        path, is_local = resolve_agentsoul_path(shared_dir, None)
        assert path == shared_dir / "AGENTSOUL.md"
        assert is_local is False


class TestReadAgentsoulWithWorkspace:
    def test_read_falls_back_to_shared(
        self, shared_dir: Path, workspace_dir: Path
    ) -> None:
        content = read_agentsoul(shared_dir, workspace_dir)
        assert "Agent Soul" in content

    def test_read_prefers_local(self, shared_dir: Path, workspace_dir: Path) -> None:
        _local_agentsoul(workspace_dir).write_text("# Local Only\n")
        content = read_agentsoul(shared_dir, workspace_dir)
        assert "Local Only" in content
        assert "Agent Soul" not in content


class TestReadAgentsoulWithSource:
    def test_shared_source(self, shared_dir: Path, workspace_dir: Path) -> None:
        content, source = read_agentsoul_with_source(shared_dir, workspace_dir)
        assert source == "shared"
        assert "Agent Soul" in content

    def test_local_source(self, shared_dir: Path, workspace_dir: Path) -> None:
        _local_agentsoul(workspace_dir).write_text("# Local Soul\n")
        content, source = read_agentsoul_with_source(shared_dir, workspace_dir)
        assert source == "local"
        assert "Local Soul" in content


class TestWriteAgentsoulToWorkspace:
    def test_write_to_workspace(self, shared_dir: Path, workspace_dir: Path) -> None:
        original_shared = read_agentsoul(shared_dir)
        write_agentsoul(shared_dir, "# Workspace Soul\n", workspace_dir=workspace_dir)
        # Workspace has the new content in .persona/
        local = workspace_dir / ".persona" / "AGENTSOUL.md"
        assert local.is_file()
        assert "Workspace Soul" in local.read_text()
        # Shared is unchanged
        assert read_agentsoul(shared_dir) == original_shared

    def test_write_without_workspace(self, shared_dir: Path) -> None:
        write_agentsoul(shared_dir, "# Updated Shared\n")
        assert "Updated Shared" in read_agentsoul(shared_dir)


class TestUpdateIdentityCopyOnWrite:
    def test_copy_on_write(self, shared_dir: Path, workspace_dir: Path) -> None:
        """update_identity with workspace_dir writes to workspace, shared unchanged."""
        original_shared = read_agentsoul(shared_dir)
        updated = update_identity(
            shared_dir, workspace_dir=workspace_dir, name="LocalBot"
        )
        assert updated.name == "LocalBot"
        # Workspace got the modified file in .persona/
        local = workspace_dir / ".persona" / "AGENTSOUL.md"
        assert local.is_file()
        assert "LocalBot" in local.read_text()
        # Shared is unchanged
        assert read_agentsoul(shared_dir) == original_shared

    def test_update_existing_local(self, shared_dir: Path, workspace_dir: Path) -> None:
        """When workspace already has AGENTSOUL.md, update_identity reads from it."""
        _local_agentsoul(workspace_dir).write_text(
            "# Agent Soul\n\n## Identity\n"
            "- **Name**: LocalBot\n"
            "- **Role**: Local Assistant\n"
            "- **Emoji**: 🏠\n"
            "- **Vibe**: cozy\n"
        )
        updated = update_identity(
            shared_dir, workspace_dir=workspace_dir, vibe="energetic"
        )
        assert updated.name == "LocalBot"  # read from local
        assert updated.vibe == "energetic"  # updated

    def test_backward_compat_no_workspace(self, shared_dir: Path) -> None:
        """Without workspace_dir, behaves exactly as before."""
        updated = update_identity(shared_dir, name="TestBot")
        assert updated.name == "TestBot"
        assert "TestBot" in read_agentsoul(shared_dir)
