"""Tests for workspace/assembler.py — ClaudeMdAssembler."""

from pathlib import Path

import pytest

from baobao.workspace.assembler import ClaudeMdAssembler
from baobao.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Initialize a workspace with templates and return the path."""
    wm = WorkspaceManager(tmp_path / "workspace")
    wm.init()
    return wm.workspace_dir


class TestAssemble:
    def test_contains_header(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        content = assembler.assemble()
        assert "BaoBao Assistant" in content
        assert "自動生成" in content

    def test_contains_all_sections(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        content = assembler.assemble()
        assert "人格 (SOUL)" in content
        assert "身份 (IDENTITY)" in content
        assert "用戶資訊 (USER)" in content
        assert "工作指令 (AGENTS)" in content
        assert "記憶 (MEMORY)" in content

    def test_includes_soul_content(self, workspace: Path) -> None:
        (workspace / "SOUL.md").write_text("# Soul\n\nTest personality")
        assembler = ClaudeMdAssembler(workspace)
        content = assembler.assemble()
        assert "Test personality" in content

    def test_includes_recent_memories(self, workspace: Path) -> None:
        from datetime import date

        today = date.today().isoformat()
        memory_dir = workspace / "memory"
        memory_dir.mkdir(exist_ok=True)
        (memory_dir / f"{today}.md").write_text("## Today\n- Something happened")

        assembler = ClaudeMdAssembler(workspace, recent_days=7)
        content = assembler.assemble()
        assert "近期記憶" in content
        assert "Something happened" in content


class TestWrite:
    def test_creates_file(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        assembler.write()
        assert (workspace / "CLAUDE.md").is_file()

    def test_file_content(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        assembler.write()
        content = (workspace / "CLAUDE.md").read_text()
        assert "BaoBao Assistant" in content


class TestNeedsRebuild:
    def test_true_when_no_output(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        assert assembler.needs_rebuild() is True

    def test_false_after_write(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

    def test_true_after_source_change(self, workspace: Path) -> None:
        assembler = ClaudeMdAssembler(workspace)
        assembler.write()

        # Modify a source file
        import time

        time.sleep(0.01)  # Ensure mtime difference
        (workspace / "SOUL.md").write_text("# Soul\n\nUpdated")
        assert assembler.needs_rebuild() is True
