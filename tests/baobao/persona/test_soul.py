"""Tests for persona/soul.py — SOUL.md read/write."""

from pathlib import Path

import pytest

from baobao.persona.soul import read_soul, write_soul
from baobao.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    wm = WorkspaceManager(tmp_path / "workspace")
    wm.init()
    return wm.workspace_dir


class TestReadSoul:
    def test_from_workspace(self, workspace: Path) -> None:
        content = read_soul(workspace)
        assert "Soul" in content

    def test_missing_file(self, tmp_path: Path) -> None:
        content = read_soul(tmp_path)
        assert content == ""


class TestWriteSoul:
    def test_write_and_read(self, workspace: Path) -> None:
        write_soul(workspace, "# Soul\n\nNew personality")
        content = read_soul(workspace)
        assert "New personality" in content

    def test_overwrites(self, workspace: Path) -> None:
        write_soul(workspace, "First")
        write_soul(workspace, "Second")
        assert read_soul(workspace) == "Second"
