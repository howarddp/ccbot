"""Tests for workspace/assembler.py — ClaudeMdAssembler."""

from pathlib import Path

import pytest

from baobaobot.workspace.assembler import ClaudeMdAssembler
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Initialize shared + workspace dirs and return (shared_dir, workspace_dir)."""
    shared = tmp_path / "shared"
    workspace = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, workspace)
    wm.init_shared()
    wm.init_workspace()
    return shared, workspace


class TestAssemble:
    def test_contains_header(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "BaoBao Assistant" in content
        assert "auto-generated" in content.lower()

    def test_contains_all_sections(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Agent Soul (AGENTSOUL)" in content
        assert "Work Instructions (AGENTS)" in content

    def test_includes_agentsoul_from_shared(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        (shared / "AGENTSOUL.md").write_text(
            "# Agent Soul\n\n## Personality\n- Test personality"
        )
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Test personality" in content

    def test_does_not_embed_memory(self, dirs: tuple[Path, Path]) -> None:
        """Memory files should NOT be embedded — Claude Code reads them on demand."""
        shared, workspace = dirs
        (workspace / "memory").mkdir(exist_ok=True)
        (workspace / "memory" / "EXPERIENCE.md").write_text(
            "# Experience\n\nRemember this"
        )
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Remember this" not in content

    def test_does_not_embed_daily_memories(self, dirs: tuple[Path, Path]) -> None:
        """Daily memory files should NOT be embedded."""
        shared, workspace = dirs
        from datetime import date

        today = date.today().isoformat()
        memory_dir = workspace / "memory"
        memory_dir.mkdir(exist_ok=True)
        (memory_dir / f"{today}.md").write_text("## Today\n- Something happened")

        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Recent Memories" not in content
        assert "Something happened" not in content

    def test_no_bin_dir_template_variable(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "{{BIN_DIR}}" not in content


class TestWrite:
    def test_creates_baobaobot_md(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert (workspace / "BAOBAOBOT.md").is_file()

    def test_creates_thin_claude_md(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert (workspace / "CLAUDE.md").is_file()
        claude_content = (workspace / "CLAUDE.md").read_text()
        assert "BAOBAOBOT.md" in claude_content

    def test_baobaobot_md_content(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        content = (workspace / "BAOBAOBOT.md").read_text()
        assert "BaoBao Assistant" in content

    def test_claude_md_is_thin(self, dirs: tuple[Path, Path]) -> None:
        """CLAUDE.md should be a short reference, not the full assembled content."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        claude_content = (workspace / "CLAUDE.md").read_text()
        baobao_content = (workspace / "BAOBAOBOT.md").read_text()
        assert len(claude_content) < len(baobao_content)
        assert "Agent Soul" not in claude_content  # Full content is in BAOBAOBOT.md


class TestNeedsRebuild:
    def test_true_when_no_output(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assert assembler.needs_rebuild() is True

    def test_false_after_write(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

    def test_true_after_shared_source_change(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()

        import time

        time.sleep(0.01)
        (shared / "AGENTSOUL.md").write_text("# Agent Soul\n\nUpdated")
        assert assembler.needs_rebuild() is True

    def test_memory_change_does_not_trigger_rebuild(
        self, dirs: tuple[Path, Path]
    ) -> None:
        """Memory changes should NOT trigger rebuild — Claude Code reads on demand."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()

        import time

        time.sleep(0.01)
        (workspace / "memory").mkdir(exist_ok=True)
        (workspace / "memory" / "EXPERIENCE.md").write_text("# Experience\n\nUpdated")
        assert assembler.needs_rebuild() is False
