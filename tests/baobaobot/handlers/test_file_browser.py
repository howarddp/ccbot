"""Tests for file_browser module."""

from baobaobot.handlers.file_browser import (
    ITEMS_PER_PAGE,
    _format_size,
    _truncate_filename,
    build_file_browser,
    clear_ls_state,
)


class TestFormatSize:
    def test_zero(self):
        assert _format_size(0) == "0B"

    def test_bytes(self):
        assert _format_size(512) == "512B"

    def test_kilobytes(self):
        assert _format_size(1024) == "1KB"
        assert _format_size(1536) == "1.5KB"

    def test_megabytes(self):
        assert _format_size(1024 * 1024) == "1MB"
        assert _format_size(int(3.4 * 1024 * 1024)) == "3.4MB"

    def test_gigabytes(self):
        assert _format_size(1024 * 1024 * 1024) == "1GB"


class TestTruncateFilename:
    def test_short_name_unchanged(self):
        assert _truncate_filename("readme.md") == "readme.md"

    def test_long_name_with_extension(self):
        result = _truncate_filename("file_20260221_214225.ogg", max_len=16)
        assert len(result) <= 16
        assert result.endswith(".ogg")
        assert "…" in result

    def test_long_name_no_extension(self):
        result = _truncate_filename("a_very_long_filename_without_ext", max_len=16)
        assert len(result) <= 16
        assert result.endswith("…")

    def test_preserves_extension(self):
        result = _truncate_filename("screenshot_2026-02-24_at_17.30.45.png", max_len=16)
        assert result.endswith(".png")
        assert "…" in result


class TestBuildFileBrowser:
    def test_lists_dirs_and_files(self, tmp_path):
        """Both directories and files appear in entries."""
        (tmp_path / "subdir").mkdir()
        (tmp_path / "file.txt").write_text("hello")
        _text, _kb, entries = build_file_browser(str(tmp_path))
        names = [e[0] for e in entries]
        assert "subdir" in names
        assert "file.txt" in names

    def test_dirs_before_files(self, tmp_path):
        """Directories should come before files."""
        (tmp_path / "zz_file.txt").write_text("x")
        (tmp_path / "aa_dir").mkdir()
        _text, _kb, entries = build_file_browser(str(tmp_path))
        dir_indices = [i for i, (_, is_dir, _s) in enumerate(entries) if is_dir]
        file_indices = [i for i, (_, is_dir, _s) in enumerate(entries) if not is_dir]
        if dir_indices and file_indices:
            assert max(dir_indices) < min(file_indices)

    def test_hidden_last(self, tmp_path):
        """Hidden items (starting with '.') should sort after non-hidden within their group."""
        (tmp_path / ".hidden_dir").mkdir()
        (tmp_path / "visible_dir").mkdir()
        (tmp_path / ".hidden_file").write_text("x")
        (tmp_path / "visible_file").write_text("x")
        _text, _kb, entries = build_file_browser(str(tmp_path))
        # Dirs: visible_dir should come before .hidden_dir
        dirs = [(n, h) for n, is_dir, _ in entries if is_dir for h in [n.startswith(".")]]
        dir_names = [n for n, _ in dirs]
        assert dir_names.index("visible_dir") < dir_names.index(".hidden_dir")
        # Files: visible_file before .hidden_file
        files = [n for n, is_dir, _ in entries if not is_dir]
        assert files.index("visible_file") < files.index(".hidden_file")

    def test_pagination(self, tmp_path):
        """More than ITEMS_PER_PAGE items triggers pagination buttons."""
        for i in range(ITEMS_PER_PAGE + 3):
            (tmp_path / f"dir_{i:02d}").mkdir()
        _text, keyboard, entries = build_file_browser(str(tmp_path), page=0)
        assert len(entries) == ITEMS_PER_PAGE + 3
        # Check page navigation exists
        all_labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        assert "▶" in all_labels
        assert "1/" in all_labels[all_labels.index("▶") - 1]  # page indicator before ▶

    def test_root_boundary(self, tmp_path):
        """Cannot go above root_path — '..' button absent at root."""
        (tmp_path / "sub").mkdir()
        _text, keyboard, _entries = build_file_browser(
            str(tmp_path), root_path=str(tmp_path)
        )
        action_labels = [btn.text for btn in keyboard.inline_keyboard[-1]]
        assert ".." not in action_labels

    def test_root_boundary_shows_up_in_subdir(self, tmp_path):
        """'..' button should appear when in a subdirectory."""
        child = tmp_path / "sub"
        child.mkdir()
        _text, keyboard, _entries = build_file_browser(
            str(child), root_path=str(tmp_path)
        )
        action_labels = [btn.text for btn in keyboard.inline_keyboard[-1]]
        assert ".." in action_labels

    def test_empty_dir(self, tmp_path):
        """Empty directory shows _(empty)_ message."""
        text, _kb, entries = build_file_browser(str(tmp_path))
        assert len(entries) == 0
        assert "_(empty)_" in text

    def test_file_size_in_text(self, tmp_path):
        """File entries in text include size."""
        f = tmp_path / "data.bin"
        f.write_bytes(b"x" * 2048)
        text, _kb, _entries = build_file_browser(str(tmp_path))
        assert "2KB" in text

    def test_close_button_always_present(self, tmp_path):
        """Close button should always be in the last row."""
        (tmp_path / "a").mkdir()
        _text, keyboard, _entries = build_file_browser(str(tmp_path))
        last_row_labels = [btn.text for btn in keyboard.inline_keyboard[-1]]
        assert "✕ 關閉" in last_row_labels


class TestClearLsState:
    def test_clears_keys(self):
        ud = {"ls_path": "/a", "ls_root": "/b", "ls_entries": [], "other": 1}
        clear_ls_state(ud)
        assert "ls_path" not in ud
        assert "ls_root" not in ud
        assert "ls_entries" not in ud
        assert ud["other"] == 1

    def test_none_safe(self):
        clear_ls_state(None)  # should not raise
