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
        assert "Personality (SOUL)" in content
        assert "Identity (IDENTITY)" in content
        assert "Work Instructions (AGENTS)" in content
        assert "Memory (MEMORY)" in content

    def test_includes_soul_from_shared(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        (shared / "SOUL.md").write_text("# Soul\n\nTest personality")
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Test personality" in content

    def test_includes_memory_from_workspace(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        (workspace / "MEMORY.md").write_text("# Memory\n\nRemember this")
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Remember this" in content

    def test_includes_recent_memories(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        from datetime import date

        today = date.today().isoformat()
        memory_dir = workspace / "memory"
        memory_dir.mkdir(exist_ok=True)
        (memory_dir / f"{today}.md").write_text("## Today\n- Something happened")

        assembler = ClaudeMdAssembler(shared, workspace, recent_days=7)
        content = assembler.assemble()
        assert "Recent Memories" in content
        assert "Something happened" in content

    def test_no_bin_dir_template_variable(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "{{BIN_DIR}}" not in content


class TestWrite:
    def test_creates_file(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert (workspace / "CLAUDE.md").is_file()

    def test_file_content(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        content = (workspace / "CLAUDE.md").read_text()
        assert "BaoBao Assistant" in content


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
        (shared / "SOUL.md").write_text("# Soul\n\nUpdated")
        assert assembler.needs_rebuild() is True

    def test_true_after_workspace_memory_change(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()

        import time

        time.sleep(0.01)
        (workspace / "MEMORY.md").write_text("# Memory\n\nUpdated")
        assert assembler.needs_rebuild() is True
