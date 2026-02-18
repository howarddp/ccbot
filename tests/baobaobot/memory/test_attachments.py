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
        # Create a source file
        src = workspace / "tmp" / "photo.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        rel = save_attachment(workspace, src, "a nice photo", "Alice")
        assert rel is not None
        assert rel.startswith("memory/attachments/")
        assert "photo.jpg" in rel

        # File was copied
        att_path = workspace / rel
        assert att_path.exists()
        assert att_path.read_bytes() == b"\xff\xd8\xff\xe0fake-jpeg"

        # Daily memory was updated
        today = date.today().isoformat()
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
        # Should NOT have the image bang prefix
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
        assert rel.startswith("memory/attachments/")


class TestAttachmentCleanup:
    def test_cleanup_attachments_for_date(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        # Create some attachments for different dates
        (att_dir / "2026-01-15_120000_photo.jpg").write_bytes(b"old")
        (att_dir / "2026-01-15_130000_doc.pdf").write_bytes(b"old2")
        (att_dir / "2026-01-16_100000_other.png").write_bytes(b"other")

        count = mm._cleanup_attachments_for_date("2026-01-15")
        assert count == 2
        # 2026-01-15 files should be gone
        assert not (att_dir / "2026-01-15_120000_photo.jpg").exists()
        assert not (att_dir / "2026-01-15_130000_doc.pdf").exists()
        # 2026-01-16 files should remain
        assert (att_dir / "2026-01-16_100000_other.png").exists()

    def test_delete_daily_cleans_attachments(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        (att_dir / "2026-02-10_090000_pic.jpg").write_bytes(b"data")
        (workspace / "memory" / "2026-02-10.md").write_text("- entry\n")

        result = mm.delete_daily("2026-02-10")
        assert result is True
        assert not (att_dir / "2026-02-10_090000_pic.jpg").exists()

    def test_delete_all_daily_cleans_all_attachments(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        (att_dir / "2026-02-10_090000_a.jpg").write_bytes(b"a")
        (att_dir / "2026-02-11_100000_b.png").write_bytes(b"b")
        (workspace / "memory" / "2026-02-10.md").write_text("- a\n")
        (workspace / "memory" / "2026-02-11.md").write_text("- b\n")

        count = mm.delete_all_daily()
        assert count == 2
        # All attachments should be gone
        remaining = list(att_dir.iterdir())
        assert len(remaining) == 0

    def test_cleanup_removes_old_attachments(
        self, workspace: Path, mm: MemoryManager
    ) -> None:
        att_dir = workspace / "memory" / "attachments"
        # Create an old daily memory + attachment
        old_date = "2020-01-01"
        (workspace / "memory" / f"{old_date}.md").write_text("- old\n")
        (att_dir / f"{old_date}_120000_old.jpg").write_bytes(b"old")

        # Create a recent daily memory + attachment (today)
        today = date.today().isoformat()
        (workspace / "memory" / f"{today}.md").write_text("- today\n")
        (att_dir / f"{today}_120000_new.jpg").write_bytes(b"new")

        count = mm.cleanup(keep_days=30)
        assert count == 1  # Only old one deleted
        # Old attachment should be gone
        assert not (att_dir / f"{old_date}_120000_old.jpg").exists()
        # Today's attachment should remain
        assert (att_dir / f"{today}_120000_new.jpg").exists()


class TestWorkspaceInit:
    def test_attachments_dir_created(self, workspace: Path) -> None:
        att_dir = workspace / "memory" / "attachments"
        assert att_dir.is_dir()
