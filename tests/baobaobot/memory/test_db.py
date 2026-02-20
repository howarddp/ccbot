"""Tests for memory/db.py — SQLite memory index."""

from pathlib import Path

import pytest

from baobaobot.memory.db import MemoryDB
from baobaobot.memory.utils import parse_tags, strip_frontmatter
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    shared = tmp_path / "shared"
    ws = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, ws)
    wm.init_shared()
    wm.init_workspace()
    return ws


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

    def test_schema_version_set(self, db: MemoryDB) -> None:
        conn = db.connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 3

    def test_attachment_meta_table_exists(self, db: MemoryDB) -> None:
        conn = db.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "attachment_meta" in names

    def test_memories_has_path_column(self, db: MemoryDB) -> None:
        conn = db.connect()
        info = conn.execute("PRAGMA table_info(memories)").fetchall()
        columns = {r["name"] for r in info}
        assert "path" in columns

    def test_file_meta_has_tags_column(self, db: MemoryDB) -> None:
        conn = db.connect()
        info = conn.execute("PRAGMA table_info(file_meta)").fetchall()
        columns = {r["name"] for r in info}
        assert "tags" in columns


class TestSync:
    def test_sync_empty_workspace(self, db: MemoryDB) -> None:
        count = db.sync()
        assert count == 0

    def test_sync_daily_file(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n- thing one\n- thing two\n")

        count = db.sync()
        assert count >= 1

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE source = 'daily' AND date = '2026-02-15'"
        ).fetchall()
        assert len(rows) == 3

    def test_sync_stores_path(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Test\n")
        db.sync()

        conn = db.connect()
        row = conn.execute(
            "SELECT path FROM memories WHERE date = '2026-02-15'"
        ).fetchone()
        assert row["path"] == "memory/2026-02-15.md"

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

    def test_sync_experience_dir(self, db: MemoryDB, workspace: Path) -> None:
        exp_dir = workspace / "memory" / "experience"
        (exp_dir / "user-preferences.md").write_text("Important note here\n")
        db.sync()

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE source = 'experience'"
        ).fetchall()
        assert len(rows) >= 1
        contents = [r["content"] for r in rows]
        assert "Important note here" in contents

    def test_sync_multiple_experience_files(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        exp_dir = workspace / "memory" / "experience"
        (exp_dir / "topic-a.md").write_text("Alpha content\n")
        (exp_dir / "topic-b.md").write_text("Beta content\n")
        db.sync()

        conn = db.connect()
        rows = conn.execute(
            "SELECT DISTINCT date FROM memories WHERE source = 'experience'"
        ).fetchall()
        dates = {r["date"] for r in rows}
        assert "topic-a" in dates
        assert "topic-b" in dates

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

    def test_cleanup_deleted_experience_file(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        exp_dir = workspace / "memory" / "experience"
        f = exp_dir / "temp-topic.md"
        f.write_text("Temporary\n")
        db.sync()

        f.unlink()
        count = db.sync()
        assert count >= 1

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE source = 'experience' AND date = 'temp-topic'"
        ).fetchall()
        assert len(rows) == 0


class TestFrontmatterStrip:
    def test_strips_frontmatter(self) -> None:
        text = "---\ndate: 2026-02-15\ntags: []\n---\n## Content\n- thing\n"
        result = strip_frontmatter(text)
        assert "---" not in result
        assert "## Content" in result

    def test_no_frontmatter(self) -> None:
        text = "## Content\n- thing\n"
        assert strip_frontmatter(text) == text

    def test_sync_strips_frontmatter(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "---\ndate: 2026-02-15\ntags: []\n---\n## Test\n- thing\n"
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute(
            "SELECT content FROM memories WHERE source = 'daily' AND date = '2026-02-15'"
        ).fetchall()
        contents = [r["content"] for r in rows]
        assert "## Test" in contents
        assert "- thing" in contents
        assert not any("date:" in c for c in contents)
        assert not any("tags:" in c for c in contents)


class TestParseTags:
    def test_frontmatter_tags(self) -> None:
        text = "---\ndate: 2026-02-15\ntags: [decision, preference]\n---\nContent\n"
        assert parse_tags(text) == ["decision", "preference"]

    def test_frontmatter_tags_with_hash(self) -> None:
        text = "---\ntags: [#decision, #bug]\n---\nContent\n"
        assert parse_tags(text) == ["bug", "decision"]

    def test_frontmatter_tags_quoted(self) -> None:
        text = '---\ntags: ["decision", "todo"]\n---\nContent\n'
        assert parse_tags(text) == ["decision", "todo"]

    def test_frontmatter_empty_tags(self) -> None:
        text = "---\ntags: []\n---\nContent\n"
        assert parse_tags(text) == []

    def test_inline_tags(self) -> None:
        text = "## Notes\n- Important #decision made today\n- Also #learning\n"
        assert parse_tags(text) == ["decision", "learning"]

    def test_inline_tags_not_headings(self) -> None:
        """Markdown headings like ## should not be matched as tags."""
        text = "## Heading\n### Sub\nSome #decision here\n"
        tags = parse_tags(text)
        assert "decision" in tags
        # "Heading" and "Sub" should NOT be tags (## is not a tag)
        assert "heading" not in tags
        assert "sub" not in tags

    def test_combined_frontmatter_and_inline(self) -> None:
        text = "---\ntags: [decision]\n---\n## Notes\nSome #learning here\n"
        assert parse_tags(text) == ["decision", "learning"]

    def test_no_tags(self) -> None:
        text = "## Just content\n- no tags at all\n"
        assert parse_tags(text) == []

    def test_tags_with_hyphens(self) -> None:
        text = "Some #project/my-app info\n"
        tags = parse_tags(text)
        assert "project/my-app" in tags

    def test_uppercase_tags_normalized(self) -> None:
        """Uppercase tags like #TODO should be normalized to lowercase."""
        text = "- #TODO fix this\n- #BUG found issue\n"
        tags = parse_tags(text)
        assert "todo" in tags
        assert "bug" in tags

    def test_frontmatter_uppercase_normalized(self) -> None:
        text = "---\ntags: [Decision, TODO]\n---\nContent\n"
        tags = parse_tags(text)
        assert tags == ["decision", "todo"]

    def test_sync_stores_tags(self, db: MemoryDB, workspace: Path) -> None:
        import json

        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "---\ndate: 2026-02-15\ntags: [decision, bug]\n---\n## Test\n"
        )
        db.sync()

        conn = db.connect()
        row = conn.execute(
            "SELECT tags FROM file_meta WHERE path = 'memory/2026-02-15.md'"
        ).fetchone()
        tags = json.loads(row["tags"])
        assert "decision" in tags
        assert "bug" in tags


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

    def test_search_experience_dir(self, db: MemoryDB, workspace: Path) -> None:
        exp_dir = workspace / "memory" / "experience"
        (exp_dir / "notes.md").write_text("Special note\n")

        results = db.search("Special")
        assert len(results) >= 1
        assert any(r["source"] == "experience" for r in results)

    def test_search_across_files(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("- shared keyword\n")
        (memory_dir / "2026-02-16.md").write_text("- shared keyword again\n")

        results = db.search("shared keyword")
        assert len(results) >= 2

    def test_search_with_tag_filter(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "---\ndate: 2026-02-15\ntags: [decision]\n---\n## Design\n- chose REST API\n"
        )
        (memory_dir / "2026-02-16.md").write_text(
            "---\ndate: 2026-02-16\ntags: [todo]\n---\n## Tasks\n- chose framework\n"
        )

        # Both have "chose"
        all_results = db.search("chose")
        assert len(all_results) == 2

        # Filter by decision tag
        decision_results = db.search("chose", tag="decision")
        assert len(decision_results) == 1
        assert decision_results[0]["date"] == "2026-02-15"

    def test_search_with_tag_no_match(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "---\ntags: [decision]\n---\n## Notes\n- content here\n"
        )

        results = db.search("content", tag="nonexistent")
        assert results == []


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


class TestListTags:
    def test_empty(self, db: MemoryDB) -> None:
        tags = db.list_tags()
        assert tags == []

    def test_lists_unique_tags(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "---\ntags: [decision, bug]\n---\n## Notes\n"
        )
        (memory_dir / "2026-02-16.md").write_text(
            "---\ntags: [decision, todo]\n---\n## Tasks\n"
        )

        tags = db.list_tags()
        assert tags == ["bug", "decision", "todo"]

    def test_includes_inline_tags(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("## Notes\n- #learning today\n")

        tags = db.list_tags()
        assert "learning" in tags


class TestGetStats:
    def test_empty_workspace(self, db: MemoryDB) -> None:
        stats = db.get_stats()
        assert stats["daily_count"] == 0

    def test_with_data(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("- thing\n")
        exp_dir = memory_dir / "experience"
        (exp_dir / "notes.md").write_text("Important\n")

        stats = db.get_stats()
        assert stats["daily_count"] == 1
        assert stats["experience_count"] == 1
        assert stats["total_lines"] >= 2
        assert stats["attachment_count"] == 0


class TestAttachments:
    def test_sync_parses_image_attachment(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "## Notes\n"
            "- ![Architecture diagram](memory/attachments/2026-02-15/arch.png)\n"
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "Architecture diagram"
        assert rows[0]["file_path"] == "memory/attachments/2026-02-15/arch.png"
        assert rows[0]["file_type"] == "image"

    def test_sync_parses_file_attachment(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "## Notes\n- [Monthly report](memory/attachments/2026-02-15/report.pdf)\n"
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "Monthly report"
        assert rows[0]["file_type"] == "file"

    def test_sync_parses_multiple_attachments(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "## Notes\n"
            "- ![Screenshot](memory/attachments/2026-02-15/shot.png)\n"
            "- [Config file](memory/attachments/2026-02-15/config.yaml)\n"
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 2
        descs = {r["description"] for r in rows}
        assert "Screenshot" in descs
        assert "Config file" in descs

    def test_ignores_non_attachment_links(self, db: MemoryDB, workspace: Path) -> None:
        """Regular markdown links (not in memory/attachments/) should be ignored."""
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "## Notes\n"
            "- See [docs](https://example.com/docs)\n"
            "- Also [readme](projects/myapp/README.md)\n"
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 0

    def test_cleanup_removes_attachment_meta(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        memory_dir = workspace / "memory"
        f = memory_dir / "2026-02-15.md"
        f.write_text("- ![img](memory/attachments/2026-02-15/shot.png)\n")
        db.sync()

        f.unlink()
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 0

    def test_list_attachments_all(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "- ![img](memory/attachments/2026-02-15/shot.png)\n"
        )
        (memory_dir / "2026-02-16.md").write_text(
            "- [doc](memory/attachments/2026-02-16/file.pdf)\n"
        )

        attachments = db.list_attachments()
        assert len(attachments) == 2

    def test_list_attachments_by_date(self, db: MemoryDB, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "- ![img](memory/attachments/2026-02-15/shot.png)\n"
        )
        (memory_dir / "2026-02-16.md").write_text(
            "- [doc](memory/attachments/2026-02-16/file.pdf)\n"
        )

        attachments = db.list_attachments(date_str="2026-02-15")
        assert len(attachments) == 1
        assert attachments[0]["description"] == "img"

    def test_stats_includes_attachment_count(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text(
            "- ![img](memory/attachments/2026-02-15/shot.png)\n"
            "- [doc](memory/attachments/2026-02-15/file.pdf)\n"
        )

        stats = db.get_stats()
        assert stats["attachment_count"] == 2


class TestSchemaSync:
    """Verify that _memory_common.py stays in sync with db.py."""

    def test_schema_version_matches(self) -> None:
        """_memory_common.py's _SCHEMA_VERSION must match db.py's."""
        import importlib.util
        from pathlib import Path as P

        from baobaobot.memory.db import _SCHEMA_VERSION as db_version

        # Load _memory_common.py as a module (it's a standalone script)
        common_path = (
            P(__file__).resolve().parents[3]
            / "src"
            / "baobaobot"
            / "workspace"
            / "bin"
            / "_memory_common.py"
        )
        spec = importlib.util.spec_from_file_location("_memory_common", common_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod._SCHEMA_VERSION == db_version, (
            f"_memory_common._SCHEMA_VERSION ({mod._SCHEMA_VERSION}) != "
            f"db._SCHEMA_VERSION ({db_version}). Keep them in sync!"
        )
