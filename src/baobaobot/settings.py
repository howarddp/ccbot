"""Multi-agent settings — reads settings.toml + .env to produce AgentConfig list.

Replaces the old env-only Config singleton with a TOML-based configuration
that supports multiple agents, each with its own bot token, tmux session,
and workspace directory tree.

Key entities:
  - AgentConfig: frozen dataclass with all resolved config for one agent.
  - load_settings(): parse .env + settings.toml → list[AgentConfig].
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .utils import baobaobot_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentConfig:
    """Resolved configuration for a single agent.

    Merges global defaults with per-agent overrides from settings.toml.
    All path attributes are pre-resolved; no further env lookups needed.
    """

    # Identity
    name: str
    agent_type: str = "claude"
    platform: str = "telegram"
    mode: str = "forum"

    # Secrets (resolved from env var name)
    bot_token: str = ""

    # Users
    allowed_users: frozenset[int] = field(default_factory=frozenset)

    # Tmux
    tmux_session_name: str = ""  # defaults to name
    tmux_main_window_name: str = "__main__"
    claude_command: str = "claude"

    # Paths (derived from config_dir + agent name)
    config_dir: Path = field(default_factory=lambda: baobaobot_dir())
    agent_dir: Path = field(default_factory=lambda: Path())

    # Monitoring
    claude_projects_path: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )
    monitor_poll_interval: float = 2.0

    # Workspace / persona
    recent_memory_days: int = 7

    # Voice
    whisper_model: str = "small"

    # Cron
    cron_default_tz: str = ""

    # Locale (e.g. "zh-TW", "en-US", "ja-JP")
    locale: str = "en-US"

    # --- Derived path helpers (use agent_dir) ---

    @property
    def state_file(self) -> Path:
        return self.agent_dir / "state.json"

    @property
    def session_map_file(self) -> Path:
        return self.agent_dir / "session_map.json"

    @property
    def monitor_state_file(self) -> Path:
        return self.agent_dir / "monitor_state.json"

    @property
    def shared_dir(self) -> Path:
        """Shared persona directory (fallback for SOUL.md etc.)."""
        return self.config_dir / "shared"

    @property
    def users_dir(self) -> Path:
        return self.shared_dir / "users"

    def workspace_dir_for(self, topic_name: str) -> Path:
        """Return the per-topic workspace directory under agent_dir."""
        safe_name = re.sub(r"[^\w\-.]", "_", topic_name).strip("._")
        safe_name = re.sub(r"_+", "_", safe_name)
        safe_name = safe_name[:100]
        if not safe_name:
            safe_name = "unnamed"
        return self.agent_dir / f"workspace_{safe_name}"

    def iter_workspace_dirs(self) -> list[Path]:
        """Return all existing per-topic workspace directories."""
        if not self.agent_dir.is_dir():
            return []
        return sorted(
            p
            for p in self.agent_dir.iterdir()
            if p.is_dir() and p.name.startswith("workspace_")
        )

    def is_user_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_users


# ---------------------------------------------------------------------------
# load_settings
# ---------------------------------------------------------------------------

# Keys that can appear in [global] and be overridden per-agent
_MERGEABLE_KEYS = {
    "allowed_users",
    "claude_command",
    "whisper_model",
    "cron_default_tz",
    "locale",
    "recent_memory_days",
    "monitor_poll_interval",
}


def load_settings(config_dir: Path | None = None) -> list[AgentConfig]:
    """Read .env + settings.toml and return a list of AgentConfig.

    Args:
        config_dir: Override for the base config directory.
                    Defaults to ``baobaobot_dir()``.

    Returns:
        List of AgentConfig, one per ``[[agents]]`` entry in settings.toml.
    """
    if config_dir is None:
        config_dir = baobaobot_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    # Load .env files (local cwd first, then config_dir)
    local_env = Path(".env")
    global_env = config_dir / ".env"
    if local_env.is_file():
        load_dotenv(local_env)
    if global_env.is_file():
        load_dotenv(global_env)

    # Read settings.toml
    toml_path = config_dir / "settings.toml"
    if not toml_path.is_file():
        raise FileNotFoundError(
            f"Settings file not found: {toml_path}\nRun 'baobaobot' to auto-create one."
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    global_section = raw.get("global", {})
    agents_list = raw.get("agents", [])
    if not agents_list:
        raise ValueError("settings.toml must contain at least one [[agents]] entry.")

    results: list[AgentConfig] = []
    for agent_raw in agents_list:
        cfg = _build_agent_config(config_dir, global_section, agent_raw)
        results.append(cfg)

    return results


def _build_agent_config(
    config_dir: Path,
    global_section: dict,
    agent_raw: dict,
) -> AgentConfig:
    """Merge global + per-agent settings into an AgentConfig."""
    name = agent_raw.get("name")
    if not name:
        raise ValueError("Each [[agents]] entry must have a 'name' field.")

    # Resolve bot token from env var
    bot_token_env = agent_raw.get("bot_token_env", "")
    bot_token = os.getenv(bot_token_env, "") if bot_token_env else ""
    if not bot_token:
        raise ValueError(
            f"Agent '{name}': bot_token_env='{bot_token_env}' "
            "is not set or empty in the environment."
        )

    # Merge allowed_users: per-agent overrides global
    raw_users = agent_raw.get("allowed_users", global_section.get("allowed_users", []))
    allowed_users = frozenset(int(u) for u in raw_users)
    if not allowed_users:
        raise ValueError(f"Agent '{name}': no allowed_users configured.")

    # Single tmux session for all agents
    tmux_session_name = "baobaobot"

    # Agent directory
    agent_dir = config_dir / "agents" / name

    # Merge the rest with global fallback
    def _get(key: str, default):
        """Agent-level > global-level > default."""
        if key in agent_raw:
            return agent_raw[key]
        return global_section.get(key, default)

    return AgentConfig(
        name=name,
        agent_type=agent_raw.get("agent_type", "claude"),
        platform=agent_raw.get("platform", "telegram"),
        mode=agent_raw.get("mode", "forum"),
        bot_token=bot_token,
        allowed_users=allowed_users,
        tmux_session_name=tmux_session_name,
        claude_command=_get("claude_command", "claude"),
        config_dir=config_dir,
        agent_dir=agent_dir,
        monitor_poll_interval=float(_get("monitor_poll_interval", 2.0)),
        recent_memory_days=int(_get("recent_memory_days", 7)),
        whisper_model=str(_get("whisper_model", "small")),
        cron_default_tz=str(_get("cron_default_tz", "")),
        locale=str(_get("locale", "en-US")),
    )
