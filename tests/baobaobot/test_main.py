"""Tests for main.main() entry point — tmux auto-launch logic."""

import sys
from unittest.mock import MagicMock, patch

from baobaobot.main import main

# Patches needed when main() reaches the bot startup path (past tmux check).
_BOT_STARTUP_PATCHES = (
    "baobaobot.main.logging",
    "baobaobot.workspace.manager.WorkspaceManager",
    "baobaobot.tmux_manager.tmux_manager",
    "baobaobot.bot.create_bot",
)


def _enter_bot_patches():
    """Context-manage all bot startup patches. Returns (mocks, exits)."""
    mocks = {}
    exits = []
    for target in _BOT_STARTUP_PATCHES:
        p = patch(target)
        m = p.start()
        mocks[target] = m
        exits.append(p)
    # create_bot must return a mock application
    mocks["baobaobot.bot.create_bot"].return_value = MagicMock()
    mocks["baobaobot.tmux_manager.tmux_manager"].get_or_create_session.return_value = (
        MagicMock(session_name="test")
    )
    return mocks, exits


class TestMainTmuxAutoLaunch:
    def test_foreground_flag_skips_tmux(self, monkeypatch):
        """--foreground bypasses _launch_in_tmux."""
        monkeypatch.setattr(sys, "argv", ["baobaobot", "--foreground"])
        monkeypatch.setenv("_BAOBAOBOT_TMUX", "")
        monkeypatch.delenv("TMUX", raising=False)
        mocks, exits = _enter_bot_patches()
        try:
            with patch("baobaobot.main._launch_in_tmux") as mock_tmux:
                main()
                mock_tmux.assert_not_called()
        finally:
            for p in exits:
                p.stop()

    def test_f_flag_skips_tmux(self, monkeypatch):
        """-f shorthand works like --foreground."""
        monkeypatch.setattr(sys, "argv", ["baobaobot", "-f"])
        monkeypatch.setenv("_BAOBAOBOT_TMUX", "")
        monkeypatch.delenv("TMUX", raising=False)
        mocks, exits = _enter_bot_patches()
        try:
            with patch("baobaobot.main._launch_in_tmux") as mock_tmux:
                main()
                mock_tmux.assert_not_called()
        finally:
            for p in exits:
                p.stop()

    def test_inside_tmux_env_skips_launch(self, monkeypatch):
        """_BAOBAOBOT_TMUX=1 skips auto-launch."""
        monkeypatch.setattr(sys, "argv", ["baobaobot"])
        monkeypatch.setenv("_BAOBAOBOT_TMUX", "1")
        monkeypatch.delenv("TMUX", raising=False)
        mocks, exits = _enter_bot_patches()
        try:
            with patch("baobaobot.main._launch_in_tmux") as mock_tmux:
                main()
                mock_tmux.assert_not_called()
        finally:
            for p in exits:
                p.stop()

    def test_tmux_env_var_skips_launch(self, monkeypatch):
        """TMUX env var present skips auto-launch."""
        monkeypatch.setattr(sys, "argv", ["baobaobot"])
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        monkeypatch.delenv("_BAOBAOBOT_TMUX", raising=False)
        mocks, exits = _enter_bot_patches()
        try:
            with patch("baobaobot.main._launch_in_tmux") as mock_tmux:
                main()
                mock_tmux.assert_not_called()
        finally:
            for p in exits:
                p.stop()


class TestMainSubcommands:
    def test_hook_subcommand_bypasses_tmux(self, monkeypatch):
        """'baobaobot hook' goes to hook_main, not tmux."""
        monkeypatch.setattr(sys, "argv", ["baobaobot", "hook"])
        with (
            patch("baobaobot.main._launch_in_tmux") as mock_tmux,
            patch("baobaobot.hook.hook_main") as mock_hook,
        ):
            main()
            mock_hook.assert_called_once()
            mock_tmux.assert_not_called()

    def test_init_subcommand_bypasses_tmux(self, monkeypatch):
        """'baobaobot init' goes to init logic, not tmux."""
        monkeypatch.setattr(sys, "argv", ["baobaobot", "init"])
        with (
            patch("baobaobot.main._launch_in_tmux") as mock_tmux,
            patch("baobaobot.workspace.manager.WorkspaceManager") as mock_wm,
            patch("baobaobot.main.print"),
        ):
            main()
            mock_wm.return_value.init_shared.assert_called_once()
            mock_tmux.assert_not_called()

    def test_setup_subcommand_bypasses_tmux(self, monkeypatch):
        """'baobaobot setup' goes to setup, not tmux."""
        monkeypatch.setattr(sys, "argv", ["baobaobot", "setup"])
        with (
            patch("baobaobot.main._launch_in_tmux") as mock_tmux,
            patch("baobaobot.main._setup") as mock_setup,
        ):
            main()
            mock_setup.assert_called_once()
            mock_tmux.assert_not_called()
