"""Tests for directory_browser.build_directory_browser with root_path support."""

from baobaobot.handlers.directory_browser import (
    DIRS_PER_PAGE,
    build_directory_browser,
)


class TestBuildDirectoryBrowser:
    def test_root_path_hides_up_button_at_root(self, tmp_path):
        """At root_path boundary, '..' button should not appear."""
        (tmp_path / "sub").mkdir()
        _text, keyboard, _dirs = build_directory_browser(
            str(tmp_path), root_path=str(tmp_path)
        )
        action_labels = [btn.text for btn in keyboard.inline_keyboard[-1]]
        assert ".." not in action_labels

    def test_root_path_shows_up_button_below_root(self, tmp_path):
        """Subdirectory of root_path should show '..' button."""
        child = tmp_path / "sub"
        child.mkdir()
        _text, keyboard, _dirs = build_directory_browser(
            str(child), root_path=str(tmp_path)
        )
        action_labels = [btn.text for btn in keyboard.inline_keyboard[-1]]
        assert ".." in action_labels

    def test_root_path_fallback_when_path_invalid(self, tmp_path):
        """Invalid current_path should fall back to root_path."""
        (tmp_path / "visible").mkdir()
        text, _keyboard, subdirs = build_directory_browser(
            "/nonexistent/path/that/does/not/exist",
            root_path=str(tmp_path),
        )
        assert "visible" in subdirs

    def test_no_root_path_shows_up_button(self, tmp_path):
        """Without root_path, '..' button shown at non-filesystem-root."""
        child = tmp_path / "sub"
        child.mkdir()
        _text, keyboard, _dirs = build_directory_browser(str(child))
        action_labels = [btn.text for btn in keyboard.inline_keyboard[-1]]
        assert ".." in action_labels

    def test_subdirs_listed_alphabetically(self, tmp_path):
        """Subdirectories should be sorted alphabetically."""
        for name in ("cherry", "apple", "banana"):
            (tmp_path / name).mkdir()
        _text, _keyboard, subdirs = build_directory_browser(str(tmp_path))
        assert subdirs == ["apple", "banana", "cherry"]

    def test_hidden_dirs_excluded(self, tmp_path):
        """Directories starting with '.' should be excluded."""
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()
        _text, _keyboard, subdirs = build_directory_browser(str(tmp_path))
        assert ".hidden" not in subdirs
        assert "visible" in subdirs

    def test_pagination(self, tmp_path):
        """DIRS_PER_PAGE respected, navigation buttons appear when needed."""
        for i in range(DIRS_PER_PAGE + 2):
            (tmp_path / f"dir_{i:02d}").mkdir()
        _text, keyboard, subdirs = build_directory_browser(str(tmp_path), page=0)
        assert len(subdirs) == DIRS_PER_PAGE + 2
        # First page: folder buttons + nav row + action row
        # Count folder buttons (2 per row)
        folder_rows = [
            row
            for row in keyboard.inline_keyboard
            if any(btn.callback_data.startswith("db:sel:") for btn in row)
        ]
        shown = sum(len(row) for row in folder_rows)
        assert shown == DIRS_PER_PAGE
        # Nav row should have a "▶" button
        all_labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        assert "▶" in all_labels
