"""Tests for memory/db.py — SQLite memory index."""

from pathlib import Path

import pytest

from baobaobot.memory.db import MemoryDB
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    wm = WorkspaceManager(tmp_path / "workspace")
    wm.init()
    return wm.workspace_dir


@pytest.fixture
def db(workspace: Path) -> MemoryDB:
    mdb = MemoryDB(workspace)
    yield mdb
    mdb.close()


class TestSchema:
    def test_creates_db_on_connect(self, db: MemoryDB) -> None:
        db.connect()
        assert db.db_path.exists()

    def test_tables_exist(self, db: MemoryDB) -> None:
        conn = db.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "memories" in names
        assert "file_meta" in names


class TestSync:
    def test_sync_empty_workspace(self, db: MemoryDB) -> None:
        # MEMORY.md exists from template
        count = db.sync()
        assert count >= 1  # At least MEMORY.md

    def test_sync_daily_file(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n- thing one\n- thing two\n")

        count = db.sync()
        assert count >= 1

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE source = 'daily' AND date = '2026-02-15'"
        ).fetchall()
        assert len(rows) == 3  # "## Test", "- thing one", "- thing two"

    def test_sync_skips_unchanged(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n- thing\n")

        db.sync()
        count = db.sync()  # Second sync — nothing changed
        assert count == 0

    def test_sync_detects_change(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        f = memory_dir / "2026-02-15.md"
        f.write_text("## Test\n- old\n")
        db.sync()

        f.write_text("## Test\n- new content\n- extra line\n")
        count = db.sync()
        assert count >= 1

        conn = db.connect()
        rows = conn.execute(
            "SELECT content FROM memories WHERE source = 'daily' AND date = '2026-02-15'"
        ).fetchall()
        contents = [r["content"] for r in rows]
        assert "- new content" in contents
        assert "- old" not in contents

    def test_sync_memory_md(self, db: MemoryDB, workspace: Path) -> None:
        (workspace / "MEMORY.md").write_text("# Memory\n\nImportant note here\n")
        db.sync()

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE source = 'memory_md'"
        ).fetchall()
        assert len(rows) >= 1
        contents = [r["content"] for r in rows]
        assert "Important note here" in contents

    def test_cleanup_deleted_file(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        f = memory_dir / "2026-02-15.md"
        f.write_text("## Test\n- will be deleted\n")
        db.sync()

        f.unlink()
        count = db.sync()
        assert count >= 1

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE date = '2026-02-15'"
        ).fetchall()
        assert len(rows) == 0


class TestSearch:
    def test_basic_search(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n- found keyword here\n")

        results = db.search("keyword")
        assert len(results) >= 1
        assert any("keyword" in r["content"] for r in results)

    def test_case_insensitive(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("Found KEYWORD here\n")

        results = db.search("keyword")
        assert len(results) >= 1

    def test_chinese_search(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## 會議\n- 討論了架構重構方案\n")

        results = db.search("架構")
        assert len(results) >= 1
        assert any("架構" in r["content"] for r in results)

    def test_no_results(self, db: MemoryDB) -> None:
        results = db.search("nonexistent_xyz_999")
        assert results == []

    def test_search_memory_md(self, db: MemoryDB, workspace: Path) -> None:
        (workspace / "MEMORY.md").write_text("# Memory\n\nSpecial note\n")

        results = db.search("Special")
        assert len(results) >= 1
        assert any(r["source"] == "memory_md" for r in results)

    def test_search_across_files(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("- shared keyword\n")
        (memory_dir / "2026-02-16.md").write_text("- shared keyword again\n")

        results = db.search("shared keyword")
        assert len(results) >= 2


class TestListDates:
    def test_empty(self, db: MemoryDB) -> None:
        dates = db.list_dates()
        assert dates == []

    def test_lists_dates(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("- thing\n")
        (memory_dir / "2026-02-16.md").write_text("- thing 1\n- thing 2\n")

        dates = db.list_dates()
        assert len(dates) == 2
        assert dates[0]["date"] == "2026-02-16"  # newest first
        assert dates[1]["date"] == "2026-02-15"


class TestGetStats:
    def test_empty_workspace(self, db: MemoryDB) -> None:
        stats = db.get_stats()
        assert stats["daily_count"] == 0

    def test_with_data(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("- thing\n")
        (workspace / "MEMORY.md").write_text("# Memory\n\nImportant\n")

        stats = db.get_stats()
        assert stats["daily_count"] == 1
        assert stats["has_longterm"] is True
        assert stats["total_lines"] >= 2
