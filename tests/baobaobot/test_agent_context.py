"""Tests for agent_context.py — AgentContext creation."""

from pathlib import Path

from baobaobot.agent_context import AgentContext, create_agent_context
from baobaobot.cron.service import CronService
from baobaobot.session import SessionManager
from baobaobot.settings import AgentConfig
from baobaobot.tmux_manager import TmuxManager


class TestCreateAgentContext:
    def test_creates_all_services(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "test"
        agent_dir.mkdir(parents=True)

        cfg = AgentConfig(
            name="test",
            bot_token="123:abc",
            allowed_users=frozenset({1}),
            tmux_session_name="test-tmux",
            claude_command="claude",
            config_dir=tmp_path,
            agent_dir=agent_dir,
        )

        ctx = create_agent_context(cfg)

        assert isinstance(ctx, AgentContext)
        assert ctx.config is cfg
        assert isinstance(ctx.tmux_manager, TmuxManager)
        assert isinstance(ctx.session_manager, SessionManager)
        assert isinstance(ctx.cron_service, CronService)
        assert ctx.session_monitor is None  # created later

    def test_tmux_manager_uses_config(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "test"
        agent_dir.mkdir(parents=True)

        cfg = AgentConfig(
            name="test",
            bot_token="123:abc",
            allowed_users=frozenset({1}),
            tmux_session_name="my-session",
            claude_command="claude --flag",
            config_dir=tmp_path,
            agent_dir=agent_dir,
        )

        ctx = create_agent_context(cfg)

        assert ctx.tmux_manager.session_name == "my-session"
        assert ctx.tmux_manager.claude_command == "claude --flag"

    def test_session_manager_uses_config(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "test"
        agent_dir.mkdir(parents=True)

        cfg = AgentConfig(
            name="test",
            bot_token="123:abc",
            allowed_users=frozenset({1}),
            tmux_session_name="test",
            config_dir=tmp_path,
            agent_dir=agent_dir,
        )

        ctx = create_agent_context(cfg)

        assert ctx.session_manager._state_file == agent_dir / "state.json"
        assert ctx.session_manager._session_map_file == agent_dir / "session_map.json"
        assert ctx.session_manager._tmux_session_name == "test"
