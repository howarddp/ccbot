"""Tests for settings.py — AgentConfig and load_settings."""

from pathlib import Path

import pytest

from baobaobot.settings import AgentConfig, load_settings


# ---------------------------------------------------------------------------
# AgentConfig unit tests
# ---------------------------------------------------------------------------


class TestAgentConfig:
    def test_derived_paths(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            config_dir=tmp_path,
            agent_dir=tmp_path / "agents" / "test",
        )
        assert cfg.state_file == tmp_path / "agents" / "test" / "state.json"
        assert cfg.session_map_file == tmp_path / "agents" / "test" / "session_map.json"
        assert (
            cfg.monitor_state_file
            == tmp_path / "agents" / "test" / "monitor_state.json"
        )
        assert cfg.shared_dir == tmp_path / "shared"
        assert cfg.users_dir == tmp_path / "shared" / "users"

    def test_workspace_dir_for_basic(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            agent_dir=tmp_path / "agents" / "test",
        )
        ws = cfg.workspace_dir_for("my-project")
        assert ws == tmp_path / "agents" / "test" / "workspace_my-project"

    def test_workspace_dir_for_sanitization(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            agent_dir=tmp_path / "agents" / "test",
        )
        # Special chars replaced with underscore
        ws = cfg.workspace_dir_for("hello world/foo")
        assert "workspace_hello_world_foo" == ws.name

    def test_workspace_dir_for_empty_name(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            agent_dir=tmp_path / "agents" / "test",
        )
        ws = cfg.workspace_dir_for("///")
        assert ws.name == "workspace_unnamed"

    def test_iter_workspace_dirs(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "test"
        agent_dir.mkdir(parents=True)
        (agent_dir / "workspace_a").mkdir()
        (agent_dir / "workspace_b").mkdir()
        (agent_dir / "not_a_workspace").mkdir()

        cfg = AgentConfig(name="test", agent_dir=agent_dir)
        dirs = cfg.iter_workspace_dirs()
        assert len(dirs) == 2
        assert all(d.name.startswith("workspace_") for d in dirs)

    def test_iter_workspace_dirs_empty(self, tmp_path: Path):
        cfg = AgentConfig(name="test", agent_dir=tmp_path / "nonexistent")
        assert cfg.iter_workspace_dirs() == []

    def test_is_user_allowed(self):
        cfg = AgentConfig(name="test", allowed_users=frozenset({111, 222}))
        assert cfg.is_user_allowed(111) is True
        assert cfg.is_user_allowed(999) is False

    def test_workspace_dir_for_special_chars(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            agent_dir=tmp_path / "agents" / "test",
        )
        ws = cfg.workspace_dir_for('a:b*c?"<>|')
        assert ":" not in ws.name
        assert "*" not in ws.name

    def test_workspace_dir_for_dots_only(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            agent_dir=tmp_path / "agents" / "test",
        )
        ws = cfg.workspace_dir_for("..")
        assert ws.name == "workspace_unnamed"

    def test_workspace_dir_for_truncates_long_name(self, tmp_path: Path):
        cfg = AgentConfig(
            name="test",
            agent_dir=tmp_path / "agents" / "test",
        )
        ws = cfg.workspace_dir_for("x" * 200)
        assert len(ws.name) <= len("workspace_") + 100

    def test_defaults(self):
        cfg = AgentConfig(name="mybot")
        assert cfg.agent_type == "claude"
        assert cfg.platform == "telegram"
        assert cfg.mode == "forum"
        assert cfg.tmux_main_window_name == "__main__"
        assert cfg.claude_command == "claude"
        assert cfg.monitor_poll_interval == 2.0
        assert cfg.show_user_messages is True
        assert cfg.auto_assemble is True
        assert cfg.whisper_model == "small"
        assert cfg.cron_default_tz == ""


# ---------------------------------------------------------------------------
# load_settings tests
# ---------------------------------------------------------------------------


def _write_settings(tmp_path: Path, toml_content: str, env_content: str = "") -> Path:
    """Helper to create a settings.toml and optional .env in tmp_path."""
    (tmp_path / "settings.toml").write_text(toml_content)
    if env_content:
        (tmp_path / ".env").write_text(env_content)
    return tmp_path


class TestLoadSettings:
    def test_basic_single_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_TOKEN", "123:abc")
        _write_settings(
            tmp_path,
            """\
[global]
allowed_users = [111, 222]
claude_command = "claude --model opus"

[[agents]]
name = "baobao"
bot_token_env = "MY_TOKEN"
""",
        )

        agents = load_settings(config_dir=tmp_path)
        assert len(agents) == 1
        cfg = agents[0]
        assert cfg.name == "baobao"
        assert cfg.bot_token == "123:abc"
        assert cfg.allowed_users == frozenset({111, 222})
        assert cfg.claude_command == "claude --model opus"
        assert cfg.tmux_session_name == "baobao"  # defaults to name
        assert cfg.agent_dir == tmp_path / "agents" / "baobao"

    def test_per_agent_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TOK_A", "a:tok")
        monkeypatch.setenv("TOK_B", "b:tok")
        _write_settings(
            tmp_path,
            """\
[global]
allowed_users = [111]
claude_command = "claude"
cron_default_tz = "UTC"

[[agents]]
name = "alpha"
bot_token_env = "TOK_A"
cron_default_tz = "Asia/Taipei"

[[agents]]
name = "beta"
bot_token_env = "TOK_B"
allowed_users = [222]
tmux_session = "beta-tmux"
""",
        )

        agents = load_settings(config_dir=tmp_path)
        assert len(agents) == 2

        alpha = agents[0]
        assert alpha.name == "alpha"
        assert alpha.cron_default_tz == "Asia/Taipei"  # overridden
        assert alpha.allowed_users == frozenset({111})  # global
        assert alpha.tmux_session_name == "alpha"  # default to name

        beta = agents[1]
        assert beta.name == "beta"
        assert beta.cron_default_tz == "UTC"  # global fallback
        assert beta.allowed_users == frozenset({222})  # overridden
        assert beta.tmux_session_name == "beta-tmux"  # custom

    def test_missing_toml_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="settings.toml"):
            load_settings(config_dir=tmp_path)

    def test_no_agents_raises(self, tmp_path: Path):
        _write_settings(tmp_path, "[global]\nallowed_users = [1]\n")
        with pytest.raises(ValueError, match="at least one"):
            load_settings(config_dir=tmp_path)

    def test_missing_name_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TOK", "x:y")
        _write_settings(
            tmp_path,
            """\
[global]
allowed_users = [1]

[[agents]]
bot_token_env = "TOK"
""",
        )
        with pytest.raises(ValueError, match="name"):
            load_settings(config_dir=tmp_path)

    def test_missing_token_raises(self, tmp_path: Path):
        _write_settings(
            tmp_path,
            """\
[global]
allowed_users = [1]

[[agents]]
name = "test"
bot_token_env = "NONEXISTENT_TOKEN_VAR"
""",
        )
        with pytest.raises(ValueError, match="bot_token_env"):
            load_settings(config_dir=tmp_path)

    def test_missing_allowed_users_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("TOK", "x:y")
        _write_settings(
            tmp_path,
            """\
[[agents]]
name = "test"
bot_token_env = "TOK"
""",
        )
        with pytest.raises(ValueError, match="allowed_users"):
            load_settings(config_dir=tmp_path)

    def test_env_file_loading(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test that .env file in config_dir is loaded."""
        _write_settings(
            tmp_path,
            """\
[global]
allowed_users = [1]

[[agents]]
name = "test"
bot_token_env = "ENV_FILE_TOKEN"
""",
            env_content="ENV_FILE_TOKEN=from-env-file:token\n",
        )
        # Ensure the env var is not already set
        monkeypatch.delenv("ENV_FILE_TOKEN", raising=False)

        agents = load_settings(config_dir=tmp_path)
        assert agents[0].bot_token == "from-env-file:token"
