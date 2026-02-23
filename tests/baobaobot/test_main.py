"""Tests for main.main() entry point — tmux auto-launch logic."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from baobaobot.main import main

# Patches needed when main() reaches the bot startup path (past tmux check).
_BOT_STARTUP_PATCHES = (
    "baobaobot.main.logging",
    "baobaobot.settings.load_settings",
    "baobaobot.workspace.manager.WorkspaceManager",
    "baobaobot.agent_context.create_agent_context",
    "baobaobot.bot.create_bot",
)


def _enter_bot_patches():
    """Context-manage all bot startup patches. Returns (mocks, exits)."""
    # Ensure settings.toml exists so main() doesn't trigger interactive _setup()
    config_dir = Path(os.environ["BAOBAOBOT_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.toml").write_text("[global]\n[[agents]]\nname = 'test'\n")

    mocks = {}
    exits = []
    for target in _BOT_STARTUP_PATCHES:
        p = patch(target)
        m = p.start()
        mocks[target] = m
        exits.append(p)
    # create_bot must return a mock application
    mocks["baobaobot.bot.create_bot"].return_value = MagicMock()
    # load_settings must return a list with a mock AgentConfig
    mock_cfg = MagicMock()
    mock_cfg.shared_dir = config_dir / "shared"
    mock_cfg.agent_dir = config_dir / "agents" / "test"
    mocks["baobaobot.settings.load_settings"].return_value = [mock_cfg]
    # create_agent_context must return a mock AgentContext with tmux_manager
    mock_ctx = MagicMock()
    mock_ctx.tmux_manager.get_or_create_session.return_value = MagicMock(
        session_name="test"
    )
    mocks["baobaobot.agent_context.create_agent_context"].return_value = mock_ctx
    return mocks, exits


class TestMainTmuxAutoLaunch:
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

    def test_tmux_env_var_still_launches(self, monkeypatch):
        """TMUX env var alone does NOT skip auto-launch (only _BAOBAOBOT_TMUX does).

        Running from inside tmux (e.g. a Claude Code session) should still
        trigger _launch_in_tmux() so the old instance gets restarted.
        """
        monkeypatch.setattr(sys, "argv", ["baobaobot"])
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        monkeypatch.delenv("_BAOBAOBOT_TMUX", raising=False)
        mocks, exits = _enter_bot_patches()
        try:
            with patch("baobaobot.main._launch_in_tmux") as mock_tmux:
                main()
                mock_tmux.assert_called_once()
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

    def test_add_agent_subcommand_bypasses_tmux(self, monkeypatch):
        """'baobaobot add-agent' goes to _add_agent, not tmux."""
        monkeypatch.setattr(sys, "argv", ["baobaobot", "add-agent"])
        with (
            patch("baobaobot.main._launch_in_tmux") as mock_tmux,
            patch("baobaobot.main._add_agent") as mock_add,
        ):
            main()
            mock_add.assert_called_once()
            mock_tmux.assert_not_called()
