"""Tests for freeze detection in status_polling."""

import time

import pytest

from baobaobot.handlers.status_polling import (
    FREEZE_TIMEOUT,
    _check_freeze,
    _window_health,
    clear_window_health,
)


@pytest.fixture(autouse=True)
def _clean_health():
    """Clear window health state before each test."""
    _window_health.clear()
    yield
    _window_health.clear()


# Pane with active spinner and no prompt — real freeze (process hung during work)
ACTIVE_SPINNER_PANE = "Some output\n\u2733 Reading file src/main.py\n"

# Pane with prompt only — normal idle
NORMAL_PANE = "Some output\n\u276f\n"

# Idle pane with old spinner output above prompt — was causing false positives
IDLE_WITH_OLD_SPINNER = (
    "Some output\n"
    "\u272b Cogitated for 1m 32s\n"
    "\n"
    "\u2500" * 30 + "\n"
    "\u276f\n"
    "\u2500" * 30 + "\n"
    "  [Opus 4.6] Context: 34%\n"
)

# Pane with Claude Code banner containing spinner char — was causing false positives
BANNER_PANE = (
    "    \u272b\n"
    "    |\n"
    "   \u25df\u2588\u25d9     Claude Code v2.1.45\n"
    " \u250c\u2588\u2588\u2588\u2510   Opus 4.6\n"
    "\n"
    "\u276f\n"
    "\u2500" * 30 + "\n"
    "  [Opus 4.6] Context: 34%\n"
)


class TestCheckFreeze:
    def test_no_freeze_on_first_check(self):
        """First check should never detect a freeze."""
        assert _check_freeze("@1", ACTIVE_SPINNER_PANE) is False

    def test_content_change_resets_timer(self):
        """Changing pane content should reset the unchanged timer."""
        _check_freeze("@1", ACTIVE_SPINNER_PANE)

        # Simulate time passing
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        # Content changes — should reset, not detect freeze
        assert _check_freeze("@1", ACTIVE_SPINNER_PANE + "new line\n") is False

    def test_freeze_detected_with_active_spinner(self):
        """Freeze detected when active spinner unchanged for timeout."""
        _check_freeze("@1", ACTIVE_SPINNER_PANE)

        # Simulate timeout elapsed
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", ACTIVE_SPINNER_PANE) is True

    def test_not_notified_twice(self):
        """Once notified, should not trigger again for the same freeze."""
        _check_freeze("@1", ACTIVE_SPINNER_PANE)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        # First detection
        assert _check_freeze("@1", ACTIVE_SPINNER_PANE) is True
        # Second check — already notified
        assert _check_freeze("@1", ACTIVE_SPINNER_PANE) is False

    def test_no_freeze_idle_with_prompt(self):
        """No freeze if pane shows idle prompt without active spinner."""
        _check_freeze("@1", NORMAL_PANE)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", NORMAL_PANE) is False

    def test_no_freeze_old_spinner_above_prompt(self):
        """No freeze for idle pane with old spinner output above prompt.

        This was the main false-positive case: Claude output like
        '✻ Cogitated for 1m 32s' contains spinner chars but is just
        historical output, not an active status spinner.
        """
        _check_freeze("@1", IDLE_WITH_OLD_SPINNER)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", IDLE_WITH_OLD_SPINNER) is False

    def test_no_freeze_banner_spinner(self):
        """No freeze for pane with spinner char in Claude Code banner.

        The welcome banner contains '✻' as part of ASCII art, which
        was being misdetected as an active spinner.
        """
        _check_freeze("@1", BANNER_PANE)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", BANNER_PANE) is False

    def test_clear_window_health_resets_state(self):
        """clear_window_health should remove tracking for a window."""
        _check_freeze("@1", ACTIVE_SPINNER_PANE)
        assert "@1" in _window_health

        clear_window_health("@1")
        assert "@1" not in _window_health

    def test_clear_nonexistent_window_is_noop(self):
        """Clearing health for unknown window should not raise."""
        clear_window_health("@nonexistent")  # should not raise
