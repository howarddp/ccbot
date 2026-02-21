"""Tests for the shared verbosity filter in verbosity_handler.py."""

from baobaobot.handlers.verbosity_handler import should_skip_message


class TestShouldSkipMessage:
    """Test message filtering by verbosity level."""

    # --- verbose: nothing skipped ---

    def test_verbose_passes_everything(self):
        assert should_skip_message("text", "assistant", "verbose") is False
        assert should_skip_message("thinking", "assistant", "verbose") is False
        assert should_skip_message("tool_use", "assistant", "verbose") is False
        assert should_skip_message("tool_result", "assistant", "verbose") is False
        assert should_skip_message("text", "user", "verbose") is False

    # --- quiet: only assistant text ---

    def test_quiet_passes_assistant_text(self):
        assert should_skip_message("text", "assistant", "quiet") is False

    def test_quiet_skips_thinking(self):
        assert should_skip_message("thinking", "assistant", "quiet") is True

    def test_quiet_skips_tool_use(self):
        assert should_skip_message("tool_use", "assistant", "quiet") is True

    def test_quiet_skips_tool_result(self):
        assert should_skip_message("tool_result", "assistant", "quiet") is True

    def test_quiet_skips_user(self):
        assert should_skip_message("text", "user", "quiet") is True

    # --- normal: assistant text + tool_use ---

    def test_normal_passes_assistant_text(self):
        assert should_skip_message("text", "assistant", "normal") is False

    def test_normal_passes_tool_use(self):
        assert should_skip_message("tool_use", "assistant", "normal") is False

    def test_normal_skips_thinking(self):
        assert should_skip_message("thinking", "assistant", "normal") is True

    def test_normal_skips_tool_result(self):
        assert should_skip_message("tool_result", "assistant", "normal") is True

    def test_normal_skips_user(self):
        assert should_skip_message("text", "user", "normal") is True


class TestShouldSkipHistoryMessage:
    """Verify that history dicts work with the same shared function."""

    def test_verbose_passes_all(self):
        assert should_skip_message("thinking", "assistant", "verbose") is False

    def test_quiet_passes_assistant_text(self):
        assert should_skip_message("text", "assistant", "quiet") is False

    def test_quiet_skips_user(self):
        assert should_skip_message("text", "user", "quiet") is True

    def test_normal_passes_tool_use(self):
        assert should_skip_message("tool_use", "assistant", "normal") is False

    def test_normal_skips_thinking(self):
        assert should_skip_message("thinking", "assistant", "normal") is True
