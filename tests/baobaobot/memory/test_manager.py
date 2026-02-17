"""Tests for memory/manager.py — MemoryManager."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from baobaobot.memory.manager import MemoryManager
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    wm = WorkspaceManager(tmp_path / "workspace")
    wm.init()
    return wm.workspace_dir


@pytest.fixture
def mm(workspace: Path) -> MemoryManager:
    return MemoryManager(workspace)


class TestListDaily:
    def test_empty(self, mm: MemoryManager) -> None:
        assert mm.list_daily() == []

    def test_lists_recent(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        today = date.today()
        for i in range(3):
            d = today - timedelta(days=i)
            (memory_dir / f"{d.isoformat()}.md").write_text(f"## Day {i}\n- thing")

        memories = mm.list_daily(days=7)
        assert len(memories) == 3
        assert memories[0].date == today.isoformat()

    def test_skips_old(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        old_date = date.today() - timedelta(days=30)
        (memory_dir / f"{old_date.isoformat()}.md").write_text("old")

        memories = mm.list_daily(days=7)
        assert len(memories) == 0


class TestGetDaily:
    def test_exists(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n- thing")

        content = mm.get_daily("2026-02-15")
        assert content is not None
        assert "thing" in content

    def test_missing(self, mm: MemoryManager) -> None:
        assert mm.get_daily("2026-01-01") is None


class TestDeleteDaily:
    def test_deletes(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("to delete")

        assert mm.delete_daily("2026-02-15") is True
        assert mm.get_daily("2026-02-15") is None

    def test_missing(self, mm: MemoryManager) -> None:
        assert mm.delete_daily("2026-01-01") is False


class TestDeleteAllDaily:
    def test_deletes_all(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        for i in range(5):
            d = date.today() - timedelta(days=i)
            (memory_dir / f"{d.isoformat()}.md").write_text("data")

        count = mm.delete_all_daily()
        assert count == 5
        assert mm.list_daily(days=30) == []


class TestSearch:
    def test_finds_in_daily(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n- found keyword here")

        results = mm.search("keyword")
        assert len(results) == 1
        assert "keyword" in results[0].line

    def test_finds_in_memory_md(self, mm: MemoryManager, workspace: Path) -> None:
        (workspace / "MEMORY.md").write_text("# Memory\n\nImportant: special note")

        results = mm.search("special")
        assert len(results) == 1
        assert results[0].file == "MEMORY.md"

    def test_case_insensitive(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("Found KEYWORD here")

        results = mm.search("keyword")
        assert len(results) == 1

    def test_no_results(self, mm: MemoryManager) -> None:
        results = mm.search("nonexistent")
        assert results == []


class TestCleanup:
    def test_removes_old(self, mm: MemoryManager, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        today = date.today()

        # Recent file
        (memory_dir / f"{today.isoformat()}.md").write_text("recent")

        # Old file
        old = today - timedelta(days=60)
        (memory_dir / f"{old.isoformat()}.md").write_text("old")

        count = mm.cleanup(keep_days=30)
        assert count == 1
        assert mm.get_daily(today.isoformat()) is not None
        assert mm.get_daily(old.isoformat()) is None


class TestGetSummary:
    def test_reads_memory_md(self, mm: MemoryManager, workspace: Path) -> None:
        summary = mm.get_summary()
        assert "Memory" in summary

    def test_missing_file(self, tmp_path: Path) -> None:
        mm = MemoryManager(tmp_path)
        assert mm.get_summary() == ""
