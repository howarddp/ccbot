"""Tests for daily memory frontmatter creation and preservation."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from baobaobot.memory.daily import append_to_daily, write_daily
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    shared = tmp_path / "shared"
    ws = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, ws)
    wm.init_shared()
    wm.init_workspace()
    return ws


class TestWriteDaily:
    def test_adds_frontmatter_to_plain_content(self, workspace: Path) -> None:
        write_daily(workspace, "2026-02-15", "## Notes\n- something")

        content = (workspace / "memory" / "2026-02-15.md").read_text()
        assert content.startswith("---\n")
        assert "date: 2026-02-15" in content
        assert "tags: []" in content
        assert "## Notes" in content

    def test_preserves_existing_frontmatter(self, workspace: Path) -> None:
        raw = "---\ndate: 2026-02-15\ntags: [decision]\n---\n## Notes\n- something"
        write_daily(workspace, "2026-02-15", raw)

        content = (workspace / "memory" / "2026-02-15.md").read_text()
        # Should NOT double the frontmatter
        assert content.count("---") == 2
        assert "tags: [decision]" in content


class TestAppendToDaily:
    def test_creates_with_frontmatter(self, workspace: Path) -> None:
        today = date.today().isoformat()
        append_to_daily(workspace, "- first entry")

        content = (workspace / "memory" / f"{today}.md").read_text()
        assert content.startswith("---\n")
        assert f"date: {today}" in content
        assert "- first entry" in content

    def test_appends_to_existing(self, workspace: Path) -> None:
        today = date.today().isoformat()
        append_to_daily(workspace, "- first")
        append_to_daily(workspace, "- second")

        content = (workspace / "memory" / f"{today}.md").read_text()
        # Should only have one frontmatter block
        assert content.count("---") == 2
        assert "- first" in content
        assert "- second" in content

    @patch("baobaobot.memory.daily.date")
    def test_uses_today_date(self, mock_date: object, workspace: Path) -> None:
        mock_date.today.return_value = date(2026, 3, 1)  # type: ignore[attr-defined]
        append_to_daily(workspace, "- entry")

        path = workspace / "memory" / "2026-03-01.md"
        assert path.exists()
        assert "date: 2026-03-01" in path.read_text()
