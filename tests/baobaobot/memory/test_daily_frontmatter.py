"""Tests for daily and experience memory frontmatter creation and preservation."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from baobaobot.memory.daily import (
    _experience_heading,
    append_to_daily,
    append_to_experience,
    write_daily,
)
from baobaobot.workspace.manager import WorkspaceManager

from .conftest import daily_file


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

        content = daily_file(workspace, "2026-02-15").read_text()
        assert content.startswith("---\n")
        assert "date: 2026-02-15" in content
        assert "tags: []" in content
        assert "## Notes" in content

    def test_preserves_existing_frontmatter(self, workspace: Path) -> None:
        raw = "---\ndate: 2026-02-15\ntags: [decision]\n---\n## Notes\n- something"
        write_daily(workspace, "2026-02-15", raw)

        content = daily_file(workspace, "2026-02-15").read_text()
        # Should NOT double the frontmatter
        assert content.count("---") == 2
        assert "tags: [decision]" in content


class TestAppendToDaily:
    def test_creates_with_frontmatter(self, workspace: Path) -> None:
        today = date.today().isoformat()
        append_to_daily(workspace, "- first entry")

        content = daily_file(workspace, today).read_text()
        assert content.startswith("---\n")
        assert f"date: {today}" in content
        assert "- first entry" in content

    def test_appends_to_existing(self, workspace: Path) -> None:
        today = date.today().isoformat()
        append_to_daily(workspace, "- first")
        append_to_daily(workspace, "- second")

        content = daily_file(workspace, today).read_text()
        # Should only have one frontmatter block
        assert content.count("---") == 2
        assert "- first" in content
        assert "- second" in content

    @patch("baobaobot.memory.daily.date")
    def test_uses_today_date(self, mock_date: object, workspace: Path) -> None:
        mock_date.today.return_value = date(2026, 3, 1)  # type: ignore[attr-defined]
        append_to_daily(workspace, "- entry")

        path = daily_file(workspace, "2026-03-01")
        assert path.exists()
        assert "date: 2026-03-01" in path.read_text()


class TestExperienceHeading:
    def test_kebab_case_english(self) -> None:
        assert _experience_heading("user-preferences") == "User Preferences"

    def test_single_word(self) -> None:
        assert _experience_heading("notes") == "Notes"

    def test_chinese_unchanged(self) -> None:
        assert _experience_heading("使用者偏好") == "使用者偏好"

    def test_mixed_stays_as_is(self) -> None:
        assert _experience_heading("專案-notes") == "專案-notes"


class TestAppendToExperience:
    def test_creates_with_frontmatter(self, workspace: Path) -> None:
        today = date.today().isoformat()
        append_to_experience(workspace, "test-topic", "some info")

        path = workspace / "memory" / "experience" / "test-topic.md"
        content = path.read_text()
        assert content.startswith("---\n")
        assert 'topic: "test-topic"' in content
        assert "tags: []" in content
        assert f"created: {today}" in content
        assert f"updated: {today}" in content
        assert "# Test Topic" in content
        assert "- some info" in content

    def test_creates_with_chinese_topic(self, workspace: Path) -> None:
        append_to_experience(workspace, "使用者偏好", "偏好設定")

        path = workspace / "memory" / "experience" / "使用者偏好.md"
        content = path.read_text()
        assert 'topic: "使用者偏好"' in content
        assert "# 使用者偏好" in content
        assert "- 偏好設定" in content

    def test_appends_and_updates_date(self, workspace: Path) -> None:
        # Create with a fake old date in frontmatter
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        path = exp_dir / "test-topic.md"
        path.write_text(
            "---\n"
            "topic: test-topic\n"
            "tags: []\n"
            "created: 2026-01-01\n"
            "updated: 2026-01-01\n"
            "---\n"
            "# Test Topic\n\n"
            "- old entry\n",
            encoding="utf-8",
        )

        today = date.today().isoformat()
        append_to_experience(workspace, "test-topic", "new entry")

        content = path.read_text()
        # created should stay unchanged
        assert "created: 2026-01-01" in content
        # updated should be bumped to today
        assert f"updated: {today}" in content
        assert "- old entry" in content
        assert "- new entry" in content

    def test_preserves_tags_on_append(self, workspace: Path) -> None:
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        path = exp_dir / "tagged.md"
        path.write_text(
            "---\n"
            "topic: tagged\n"
            "tags: [decision, architecture]\n"
            "created: 2026-01-01\n"
            "updated: 2026-01-01\n"
            "---\n"
            "# Tagged\n\n",
            encoding="utf-8",
        )

        append_to_experience(workspace, "tagged", "more info")

        content = path.read_text()
        assert "tags: [decision, architecture]" in content

    def test_user_tag_in_content(self, workspace: Path) -> None:
        append_to_experience(workspace, "notes", "important", user_name="Alice")

        path = workspace / "memory" / "experience" / "notes.md"
        content = path.read_text()
        assert "- [Alice] important" in content

    def test_appends_cleanly_without_trailing_newline(self, workspace: Path) -> None:
        """File without trailing newline should not merge lines on append."""
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        path = exp_dir / "no-newline.md"
        path.write_text(
            "---\n"
            'topic: "no-newline"\n'
            "tags: []\n"
            "created: 2026-01-01\n"
            "updated: 2026-01-01\n"
            "---\n"
            "# No Newline\n\n"
            "- old entry",  # no trailing newline
            encoding="utf-8",
        )

        append_to_experience(workspace, "no-newline", "new entry")

        content = path.read_text()
        assert "- old entry\n- new entry\n" in content

    def test_existing_file_without_frontmatter(self, workspace: Path) -> None:
        """Existing files without frontmatter should still get content appended."""
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        path = exp_dir / "legacy.md"
        path.write_text("# Legacy\n\n- old stuff\n", encoding="utf-8")

        append_to_experience(workspace, "legacy", "new stuff")

        content = path.read_text()
        # No updated field to replace, so content is just appended
        assert "- old stuff" in content
        assert "- new stuff" in content
