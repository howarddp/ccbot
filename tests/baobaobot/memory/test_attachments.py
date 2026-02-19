"""Tests for memory attachment support — save_attachment() and cleanup."""

from datetime import date
from pathlib import Path
import pytest

from baobaobot.memory.daily import save_attachment
from baobaobot.memory.manager import MemoryManager
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
def mm(workspace: Path) -> MemoryManager:
    return MemoryManager(workspace)


class TestSaveAttachment:
    def test_copies_file_and_writes_daily(self, workspace: Path) -> None:
        src = workspace / "tmp" / "photo.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        rel = save_attachment(workspace, src, "a nice photo", "Alice")
        assert rel is not None
        today = date.today().isoformat()
        # Preserves original filename in date subdirectory
        assert rel == f"memory/attachments/{today}/photo.jpg"

        # File was copied
        att_path = workspace / rel
        assert att_path.exists()
        assert att_path.read_bytes() == b"\xff\xd8\xff\xe0fake-jpeg"

        # Daily memory was updated
        daily = (workspace / "memory" / f"{today}.md").read_text()
        assert "![a nice photo]" in daily
        assert "[Alice]" in daily

    def test_image_uses_bang_syntax(self, workspace: Path) -> None:
        for ext in (".jpg", ".png", ".gif", ".webp"):
            src = workspace / "tmp" / f"img{ext}"
            src.write_bytes(b"data")
            rel = save_attachment(workspace, src, f"test{ext}")
            assert rel is not None
            daily = (
                workspace / "memory" / f"{date.today().isoformat()}.md"
            ).read_text()
            assert f"![test{ext}]" in daily

    def test_non_image_uses_link_syntax(self, workspace: Path) -> None:
        src = workspace / "tmp" / "report.pdf"
        src.write_bytes(b"%PDF-1.4")
        rel = save_attachment(workspace, src, "monthly report")
        assert rel is not None
        daily = (workspace / "memory" / f"{date.today().isoformat()}.md").read_text()
        assert "[monthly report](" in daily
        assert "![monthly report]" not in daily

    def test_source_not_found_returns_none(self, workspace: Path) -> None:
        missing = workspace / "tmp" / "nonexistent.txt"
        result = save_attachment(workspace, missing, "missing file")
        assert result is None

    def test_returns_relative_path(self, workspace: Path) -> None:
        src = workspace / "tmp" / "data.csv"
        src.write_bytes(b"a,b,c\n1,2,3")
        rel = save_attachment(workspace, src, "data file")
        assert rel is not None
        assert not rel.startswith("/")
        today = date.today().isoformat()
        assert rel == f"memory/attachments/{today}/data.csv"

    def test_collision_adds_numeric_suffix(self, workspace: Path) -> None:
        src = workspace / "tmp" / "file.txt"
        src.write_bytes(b"first")

        rel1 = save_attachment(workspace, src, "first copy")
        assert rel1 is not None
        today = date.today().isoformat()
        assert rel1 == f"memory/attachments/{today}/file.txt"

        # Save again — same source filename
        src.write_bytes(b"second")
        rel2 = save_attachment(workspace, src, "second copy")
        assert rel2 is not None
        assert rel2 == f"memory/attachments/{today}/file_2.txt"

        # Third time
        src.write_bytes(b"third")
        rel3 = save_attachment(workspace, src, "third copy")
        assert rel3 is not None
        assert rel3 == f"memory/attachments/{today}/file_3.txt"

    def test_strips_tmp_timestamp_prefix(self, workspace: Path) -> None:
        # Simulate file downloaded by bot.py with YYYYMMDD_HHMMSS_ prefix
        src = workspace / "tmp" / "20260219_120346_Resume.pdf"
        src.write_bytes(b"%PDF")

        rel = save_attachment(workspace, src, "resume")
        assert rel is not None
        today = date.today().isoformat()
        # Prefix should be stripped
        assert rel == f"memory/attachments/{today}/Resume.pdf"

    def test_preserves_name_without_tmp_prefix(self, workspace: Path) -> None:
        # File without tmp prefix should be unchanged
        src = workspace / "tmp" / "my_report_2026.pdf"
        src.write_bytes(b"%PDF")

        rel = save_attachment(workspace, src, "report")
        assert rel is not None
        today = date.today().isoformat()
        assert rel == f"memory/attachments/{today}/my_report_2026.pdf"


class TestAttachmentCleanup:
    def test_cleanup_attachments_for_date(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        (att_dir / "2026-01-15").mkdir(parents=True, exist_ok=True)
        (att_dir / "2026-01-15" / "photo.jpg").write_bytes(b"old")
        (att_dir / "2026-01-15" / "doc.pdf").write_bytes(b"old2")
        (att_dir / "2026-01-16").mkdir(parents=True, exist_ok=True)
        (att_dir / "2026-01-16" / "other.png").write_bytes(b"other")

        count = mm._cleanup_attachments_for_date("2026-01-15")
        assert count == 2
        assert not (att_dir / "2026-01-15").exists()
        assert (att_dir / "2026-01-16" / "other.png").exists()

    def test_delete_daily_cleans_attachments(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        (att_dir / "2026-02-10").mkdir(parents=True, exist_ok=True)
        (att_dir / "2026-02-10" / "pic.jpg").write_bytes(b"data")
        (workspace / "memory" / "2026-02-10.md").write_text("- entry\n")

        result = mm.delete_daily("2026-02-10")
        assert result is True
        assert not (att_dir / "2026-02-10").exists()

    def test_delete_all_daily_cleans_all_attachments(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        (att_dir / "2026-02-10").mkdir(parents=True, exist_ok=True)
        (att_dir / "2026-02-10" / "a.jpg").write_bytes(b"a")
        (att_dir / "2026-02-11").mkdir(parents=True, exist_ok=True)
        (att_dir / "2026-02-11" / "b.png").write_bytes(b"b")
        (workspace / "memory" / "2026-02-10.md").write_text("- a\n")
        (workspace / "memory" / "2026-02-11.md").write_text("- b\n")

        count = mm.delete_all_daily()
        assert count == 2
        remaining = [d for d in att_dir.iterdir() if d.is_dir()]
        assert len(remaining) == 0

    def test_cleanup_removes_old_attachments(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        old_date = "2020-01-01"
        (workspace / "memory" / f"{old_date}.md").write_text("- old\n")
        (att_dir / old_date).mkdir(parents=True, exist_ok=True)
        (att_dir / old_date / "old.jpg").write_bytes(b"old")

        today = date.today().isoformat()
        (workspace / "memory" / f"{today}.md").write_text("- today\n")
        (att_dir / today).mkdir(parents=True, exist_ok=True)
        (att_dir / today / "new.jpg").write_bytes(b"new")

        count = mm.cleanup(keep_days=30)
        assert count == 1
        assert not (att_dir / old_date).exists()
        assert (att_dir / today / "new.jpg").exists()


class TestWorkspaceInit:
    def test_attachments_dir_created(self, workspace: Path) -> None:
        att_dir = workspace / "memory" / "attachments"
        assert att_dir.is_dir()
