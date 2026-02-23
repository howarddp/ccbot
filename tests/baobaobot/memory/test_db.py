"""Tests for memory/db.py — SQLite memory index."""

from pathlib import Path

import pytest

from baobaobot.memory.db import MemoryDB
from baobaobot.memory.utils import parse_tags, strip_frontmatter
from baobaobot.workspace.manager import WorkspaceManager

from .conftest import write_daily


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
        assert version >= 4

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
        write_daily(workspace, "2026-02-15", "## Test\n- thing one\n- thing two\n")

        count = db.sync()
        assert count >= 1

        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE source = 'daily' AND date = '2026-02-15'"
        ).fetchall()
        assert len(rows) == 3

    def test_sync_stores_path(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "## Test\n")
        db.sync()

        conn = db.connect()
        row = conn.execute(
            "SELECT path FROM memories WHERE date = '2026-02-15'"
        ).fetchone()
        assert row["path"] == "memory/daily/2026-02/2026-02-15.md"

    def test_sync_skips_unchanged(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "## Test\n- thing\n")

        db.sync()
        count = db.sync()  # Second sync — nothing changed
        assert count == 0

    def test_sync_detects_change(self, db: MemoryDB, workspace: Path) -> None:
        f = write_daily(workspace, "2026-02-15", "## Test\n- old\n")
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
        f = write_daily(workspace, "2026-02-15", "## Test\n- will be deleted\n")
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

    def test_migrates_legacy_daily_files(self, db: MemoryDB, workspace: Path) -> None:
        """Legacy memory/YYYY-MM-DD.md files should be auto-migrated on sync."""
        memory_dir = workspace / "memory"
        (memory_dir / "2026-01-10.md").write_text("## Old format\n- legacy\n")

        db.sync()

        # Legacy file should be moved
        assert not (memory_dir / "2026-01-10.md").exists()
        assert (memory_dir / "daily" / "2026-01" / "2026-01-10.md").exists()

        # Content should be indexed
        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE date = '2026-01-10'"
        ).fetchall()
        assert len(rows) >= 1

    def test_migrates_day_only_files(self, db: MemoryDB, workspace: Path) -> None:
        """Old-format daily/YYYY-MM/DD.md files should be migrated to YYYY-MM-DD.md."""
        day_only = workspace / "memory" / "daily" / "2026-01" / "10.md"
        day_only.parent.mkdir(parents=True, exist_ok=True)
        day_only.write_text("## Day-only format\n- old\n")

        db.sync()

        # Old DD.md should be renamed
        assert not day_only.exists()
        new_path = workspace / "memory" / "daily" / "2026-01" / "2026-01-10.md"
        assert new_path.exists()

        # Content should be indexed
        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memories WHERE date = '2026-01-10'"
        ).fetchall()
        assert len(rows) >= 1


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
        write_daily(
            workspace,
            "2026-02-15",
            "---\ndate: 2026-02-15\ntags: []\n---\n## Test\n- thing\n",
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

        write_daily(
            workspace,
            "2026-02-15",
            "---\ndate: 2026-02-15\ntags: [decision, bug]\n---\n## Test\n",
        )
        db.sync()

        conn = db.connect()
        row = conn.execute(
            "SELECT tags FROM file_meta WHERE path = 'memory/daily/2026-02/2026-02-15.md'"
        ).fetchone()
        tags = json.loads(row["tags"])
        assert "decision" in tags
        assert "bug" in tags


class TestSearch:
    def test_basic_search(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "## Test\n- found keyword here\n")

        results = db.search("keyword")
        assert len(results) >= 1
        assert any("keyword" in r["content"] for r in results)

    def test_case_insensitive(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "Found KEYWORD here\n")

        results = db.search("keyword")
        assert len(results) >= 1

    def test_chinese_search(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "## 會議\n- 討論了架構重構方案\n")

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
        # Content must be sufficiently distinct to survive dedup
        write_daily(workspace, "2026-02-15", "- shared keyword alpha version\n")
        write_daily(workspace, "2026-02-16", "- shared keyword beta release\n")

        results = db.search("shared keyword")
        assert len(results) >= 2

    def test_search_with_tag_filter(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "---\ndate: 2026-02-15\ntags: [decision]\n---\n## Design\n- chose REST API\n",
        )
        write_daily(
            workspace,
            "2026-02-16",
            "---\ndate: 2026-02-16\ntags: [todo]\n---\n## Tasks\n- chose framework\n",
        )

        all_results = db.search("chose")
        assert len(all_results) == 2

        decision_results = db.search("chose", tag="decision")
        assert len(decision_results) == 1
        assert decision_results[0]["date"] == "2026-02-15"

    def test_search_with_tag_no_match(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "---\ntags: [decision]\n---\n## Notes\n- content here\n",
        )

        results = db.search("content", tag="nonexistent")
        assert results == []

    def test_search_days_includes_summaries(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        """--days search should return results from both daily and summary files."""
        from datetime import date as dt_date, timedelta

        today = dt_date.today()
        recent = (today - timedelta(days=2)).isoformat()

        # Create a recent daily file (distinct content to avoid dedup)
        write_daily(workspace, recent, "- daily unique keyword alpha version\n")

        # Create a recent summary file (distinct content to avoid dedup)
        summaries_dir = workspace / "memory" / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        summary_file = summaries_dir / f"{recent}_1400.md"
        summary_file.write_text("- summary unique keyword beta release\n")

        results = db.search("unique keyword", days=7)
        sources = {r["source"] for r in results}
        assert "daily" in sources
        assert "summary" in sources
        assert len(results) >= 2


class TestListDates:
    def test_empty(self, db: MemoryDB) -> None:
        dates = db.list_dates()
        assert dates == []

    def test_lists_dates(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "- thing\n")
        write_daily(workspace, "2026-02-16", "- thing 1\n- thing 2\n")

        dates = db.list_dates()
        assert len(dates) == 2
        assert dates[0]["date"] == "2026-02-16"  # newest first
        assert dates[1]["date"] == "2026-02-15"


class TestListTags:
    def test_empty(self, db: MemoryDB) -> None:
        tags = db.list_tags()
        assert tags == []

    def test_lists_unique_tags(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "---\ntags: [decision, bug]\n---\n## Notes\n",
        )
        write_daily(
            workspace,
            "2026-02-16",
            "---\ntags: [decision, todo]\n---\n## Tasks\n",
        )

        tags = db.list_tags()
        assert tags == ["bug", "decision", "todo"]

    def test_includes_inline_tags(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "## Notes\n- #learning today\n")

        tags = db.list_tags()
        assert "learning" in tags


class TestGetStats:
    def test_empty_workspace(self, db: MemoryDB) -> None:
        stats = db.get_stats()
        assert stats["daily_count"] == 0

    def test_with_data(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "- thing\n")
        exp_dir = workspace / "memory" / "experience"
        (exp_dir / "notes.md").write_text("Important\n")

        stats = db.get_stats()
        assert stats["daily_count"] == 1
        assert stats["experience_count"] == 1
        assert stats["total_lines"] >= 2
        assert stats["attachment_count"] == 0


class TestAttachments:
    def test_sync_parses_image_attachment(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "## Notes\n"
            "- ![Architecture diagram](memory/attachments/2026-02-15/arch.png)\n",
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "Architecture diagram"
        assert rows[0]["file_path"] == "memory/attachments/2026-02-15/arch.png"
        assert rows[0]["file_type"] == "image"

    def test_sync_parses_file_attachment(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "## Notes\n- [Monthly report](memory/attachments/2026-02-15/report.pdf)\n",
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
        write_daily(
            workspace,
            "2026-02-15",
            "## Notes\n"
            "- ![Screenshot](memory/attachments/2026-02-15/shot.png)\n"
            "- [Config file](memory/attachments/2026-02-15/config.yaml)\n",
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 2
        descs = {r["description"] for r in rows}
        assert "Screenshot" in descs
        assert "Config file" in descs

    def test_ignores_non_attachment_links(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "## Notes\n"
            "- See [docs](https://example.com/docs)\n"
            "- Also [readme](projects/myapp/README.md)\n",
        )
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 0

    def test_cleanup_removes_attachment_meta(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        f = write_daily(
            workspace,
            "2026-02-15",
            "- ![img](memory/attachments/2026-02-15/shot.png)\n",
        )
        db.sync()

        f.unlink()
        db.sync()

        conn = db.connect()
        rows = conn.execute("SELECT * FROM attachment_meta").fetchall()
        assert len(rows) == 0

    def test_list_attachments_all(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "- ![img](memory/attachments/2026-02-15/shot.png)\n",
        )
        write_daily(
            workspace,
            "2026-02-16",
            "- [doc](memory/attachments/2026-02-16/file.pdf)\n",
        )

        attachments = db.list_attachments()
        assert len(attachments) == 2

    def test_list_attachments_by_date(self, db: MemoryDB, workspace: Path) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "- ![img](memory/attachments/2026-02-15/shot.png)\n",
        )
        write_daily(
            workspace,
            "2026-02-16",
            "- [doc](memory/attachments/2026-02-16/file.pdf)\n",
        )

        attachments = db.list_attachments(date_str="2026-02-15")
        assert len(attachments) == 1
        assert attachments[0]["description"] == "img"

    def test_stats_includes_attachment_count(
        self, db: MemoryDB, workspace: Path
    ) -> None:
        write_daily(
            workspace,
            "2026-02-15",
            "- ![img](memory/attachments/2026-02-15/shot.png)\n"
            "- [doc](memory/attachments/2026-02-15/file.pdf)\n",
        )

        stats = db.get_stats()
        assert stats["attachment_count"] == 2


class TestSchemaSync:
    """Verify that _memory_common.py stays in sync with db.py."""

    @staticmethod
    def _load_common_module():
        """Load _memory_common.py as a module."""
        import importlib.util
        from pathlib import Path as P

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
        return mod

    def test_schema_version_matches(self) -> None:
        """_memory_common.py's _SCHEMA_VERSION must match db.py's."""
        from baobaobot.memory.db import _SCHEMA_VERSION as db_version

        mod = self._load_common_module()

        assert mod._SCHEMA_VERSION == db_version, (
            f"_memory_common._SCHEMA_VERSION ({mod._SCHEMA_VERSION}) != "
            f"db._SCHEMA_VERSION ({db_version}). Keep them in sync!"
        )

    def test_source_priority_matches(self) -> None:
        """_memory_common.py's _SOURCE_PRIORITY must match db.py's."""
        from baobaobot.memory.db import _SOURCE_PRIORITY as db_prio

        mod = self._load_common_module()

        assert mod._SOURCE_PRIORITY == db_prio, (
            f"_memory_common._SOURCE_PRIORITY ({mod._SOURCE_PRIORITY}) != "
            f"db._SOURCE_PRIORITY ({db_prio}). Keep them in sync!"
        )

    def test_dedup_results_behavior_matches(self) -> None:
        """Both _dedup_results implementations must produce identical output."""
        from baobaobot.memory.db import _dedup_results as db_dedup

        mod = self._load_common_module()
        common_dedup = mod._dedup_results

        test_input = [
            {
                "source": "experience",
                "date": "topic",
                "line_num": 1,
                "content": "討論了架構重構方案",
            },
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 3,
                "content": "討論了架構重構方案",
            },
            {
                "source": "summary",
                "date": "2026-02-15_1400",
                "line_num": 1,
                "content": "完全不同的東西",
            },
        ]
        assert db_dedup(test_input) == common_dedup(test_input)


class TestDedupHelpers:
    """Unit tests for dedup helper functions."""

    def test_char_bigrams_ascii(self) -> None:
        from baobaobot.memory.db import _char_bigrams

        result = _char_bigrams("hello")
        assert result == {"he", "el", "ll", "lo"}

    def test_char_bigrams_cjk(self) -> None:
        from baobaobot.memory.db import _char_bigrams

        result = _char_bigrams("架構重構")
        assert result == {"架構", "構重", "重構"}

    def test_char_bigrams_strips_markdown(self) -> None:
        from baobaobot.memory.db import _char_bigrams

        result = _char_bigrams("## heading")
        # After stripping # and space: "heading"
        assert result == {"he", "ea", "ad", "di", "in", "ng"}

    def test_char_bigrams_single_char(self) -> None:
        from baobaobot.memory.db import _char_bigrams

        assert _char_bigrams("a") == set()

    def test_char_bigrams_empty(self) -> None:
        from baobaobot.memory.db import _char_bigrams

        assert _char_bigrams("") == set()

    def test_jaccard_identical(self) -> None:
        from baobaobot.memory.db import _jaccard

        s = {"ab", "bc", "cd"}
        assert _jaccard(s, s) == 1.0

    def test_jaccard_disjoint(self) -> None:
        from baobaobot.memory.db import _jaccard

        assert _jaccard({"ab", "bc"}, {"xy", "yz"}) == 0.0

    def test_jaccard_partial(self) -> None:
        from baobaobot.memory.db import _jaccard

        a = {"ab", "bc", "cd"}
        b = {"ab", "bc", "xy"}
        # intersection=2, union=4
        assert _jaccard(a, b) == pytest.approx(0.5)

    def test_jaccard_both_empty(self) -> None:
        from baobaobot.memory.db import _jaccard

        assert _jaccard(set(), set()) == 1.0

    def test_jaccard_one_empty(self) -> None:
        from baobaobot.memory.db import _jaccard

        assert _jaccard({"ab"}, set()) == 0.0


class TestDedup:
    """Integration tests for _dedup_results."""

    def test_empty_results(self) -> None:
        from baobaobot.memory.db import _dedup_results

        assert _dedup_results([]) == []

    def test_single_result(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {"source": "daily", "date": "2026-02-15", "line_num": 1, "content": "hello"}
        ]
        assert _dedup_results(results) == results

    def test_keeps_distinct_results(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 1,
                "content": "架構重構方案",
            },
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 2,
                "content": "完全不同的內容",
            },
        ]
        assert len(_dedup_results(results)) == 2

    def test_dedup_same_content_cross_source(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {
                "source": "experience",
                "date": "topic",
                "line_num": 1,
                "content": "討論了架構重構方案",
            },
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 3,
                "content": "討論了架構重構方案",
            },
            {
                "source": "summary",
                "date": "2026-02-15_1400",
                "line_num": 1,
                "content": "討論了架構重構方案",
            },
        ]
        deduped = _dedup_results(results)
        assert len(deduped) == 1
        assert deduped[0]["source"] == "experience"

    def test_dedup_near_duplicate(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 1,
                "content": "- 討論了架構重構方案",
            },
            {
                "source": "summary",
                "date": "2026-02-15_1400",
                "line_num": 1,
                "content": "討論架構重構方案",
            },
        ]
        deduped = _dedup_results(results)
        assert len(deduped) == 1
        assert deduped[0]["source"] == "daily"

    def test_priority_experience_over_daily(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 1,
                "content": "same content here",
            },
            {
                "source": "experience",
                "date": "topic",
                "line_num": 1,
                "content": "same content here",
            },
        ]
        deduped = _dedup_results(results)
        assert len(deduped) == 1
        assert deduped[0]["source"] == "experience"

    def test_priority_daily_over_summary(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {
                "source": "summary",
                "date": "2026-02-15_1400",
                "line_num": 1,
                "content": "same content here",
            },
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 1,
                "content": "same content here",
            },
        ]
        deduped = _dedup_results(results)
        assert len(deduped) == 1
        assert deduped[0]["source"] == "daily"

    def test_custom_threshold(self) -> None:
        from baobaobot.memory.db import _dedup_results

        results = [
            {
                "source": "daily",
                "date": "2026-02-15",
                "line_num": 1,
                "content": "abcdefgh",
            },
            {
                "source": "summary",
                "date": "2026-02-15_1400",
                "line_num": 1,
                "content": "abcxyzgh",
            },
        ]
        # With high threshold they're distinct
        assert len(_dedup_results(results, threshold=0.9)) == 2
        # With low threshold they'd be deduped
        assert len(_dedup_results(results, threshold=0.1)) == 1

    def test_search_deduplicates(self, db: MemoryDB, workspace: Path) -> None:
        """End-to-end: search results are deduplicated across sources."""
        # Write same content to daily + experience + summary
        write_daily(workspace, "2026-02-15", "- 討論了架構重構方案\n")

        exp_dir = workspace / "memory" / "experience"
        (exp_dir / "arch.md").write_text("- 討論了架構重構方案\n")

        summaries_dir = workspace / "memory" / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        (summaries_dir / "2026-02-15_1400.md").write_text("- 討論了架構重構方案\n")

        results = db.search("架構重構")
        # Should be deduped to 1 result (experience wins)
        assert len(results) == 1
        assert results[0]["source"] == "experience"
