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


FROZEN_PANE = (
    "Some output\n"
    "\u2733 Reading file src/main.py\n"
    "\u2500" * 30 + "\n"
    "\u276f\n"
    "\u2500" * 30 + "\n"
    "  [Opus 4.6] Context: 34%\n"
)

NORMAL_PANE = "Some output\n\u276f\n"

SPINNER_ONLY_PANE = "Some output\n\u2733 Working\n"


class TestCheckFreeze:
    def test_no_freeze_on_first_check(self):
        """First check should never detect a freeze."""
        assert _check_freeze("@1", FROZEN_PANE) is False

    def test_content_change_resets_timer(self):
        """Changing pane content should reset the unchanged timer."""
        _check_freeze("@1", FROZEN_PANE)

        # Simulate time passing
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        # Content changes — should reset, not detect freeze
        assert _check_freeze("@1", FROZEN_PANE + "new line\n") is False

    def test_freeze_detected_after_timeout(self):
        """Freeze should be detected after timeout with unchanged content."""
        _check_freeze("@1", FROZEN_PANE)

        # Simulate timeout elapsed
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", FROZEN_PANE) is True

    def test_not_notified_twice(self):
        """Once notified, should not trigger again for the same freeze."""
        _check_freeze("@1", FROZEN_PANE)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        # First detection
        assert _check_freeze("@1", FROZEN_PANE) is True
        # Second check — already notified
        assert _check_freeze("@1", FROZEN_PANE) is False

    def test_no_freeze_without_prompt(self):
        """No freeze if pane has spinner but no ❯ prompt."""
        _check_freeze("@1", SPINNER_ONLY_PANE)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", SPINNER_ONLY_PANE) is False

    def test_no_freeze_without_spinner(self):
        """No freeze if pane has ❯ prompt but no spinner."""
        _check_freeze("@1", NORMAL_PANE)
        _window_health["@1"].unchanged_since = time.monotonic() - FREEZE_TIMEOUT - 1

        assert _check_freeze("@1", NORMAL_PANE) is False

    def test_clear_window_health_resets_state(self):
        """clear_window_health should remove tracking for a window."""
        _check_freeze("@1", FROZEN_PANE)
        assert "@1" in _window_health

        clear_window_health("@1")
        assert "@1" not in _window_health

    def test_clear_nonexistent_window_is_noop(self):
        """Clearing health for unknown window should not raise."""
        clear_window_health("@nonexistent")  # should not raise
