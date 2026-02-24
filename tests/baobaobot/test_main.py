"""Tests for main.main() entry point — tmux auto-launch logic."""

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from baobaobot.main import _check_optional_deps, main

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


class TestCheckOptionalDeps:
    def test_no_whisper_model_skips_check(self, caplog):
        """No agent uses whisper_model — no import attempted, no warning."""
        cfg = SimpleNamespace(whisper_model="")
        with caplog.at_level(logging.WARNING):
            _check_optional_deps([cfg])
        assert "faster-whisper" not in caplog.text

    def test_whisper_available_no_warning(self, caplog):
        """whisper_model set and faster_whisper importable — no warning."""
        cfg = SimpleNamespace(whisper_model="small")
        with (
            patch.dict("sys.modules", {"faster_whisper": MagicMock()}),
            caplog.at_level(logging.WARNING),
        ):
            _check_optional_deps([cfg])
        assert "faster-whisper" not in caplog.text

    def test_whisper_missing_logs_warning(self, caplog):
        """whisper_model set but faster_whisper missing — logs warning."""
        cfg = SimpleNamespace(whisper_model="small")
        with (
            patch.dict("sys.modules", {"faster_whisper": None}),
            patch("builtins.__import__", side_effect=_fake_import),
            caplog.at_level(logging.WARNING),
        ):
            _check_optional_deps([cfg])
        assert "faster-whisper not installed" in caplog.text
        assert "uv sync --extra voice" in caplog.text


def _fake_import(name, *args, **kwargs):
    """Raise ImportError only for faster_whisper."""
    if name == "faster_whisper":
        raise ImportError("No module named 'faster_whisper'")
    return __builtins__.__import__(name, *args, **kwargs)  # type: ignore[union-attr]
