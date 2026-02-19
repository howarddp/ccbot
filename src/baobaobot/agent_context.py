"""AgentContext — bundles per-agent config with its service instances.

Each agent gets one AgentContext that holds the resolved AgentConfig
plus the TmuxManager, SessionManager, SessionMonitor, and CronService
instances that operate on that agent's state.

Used by bot.py and handlers via ``context.bot_data["agent_ctx"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .cron.service import CronService
    from .session import SessionManager
    from .session_monitor import SessionMonitor
    from .settings import AgentConfig
    from .tmux_manager import TmuxManager


@dataclass
class MessageQueueState:
    """Per-agent message queue state.

    Avoids module-level globals so multiple agents don't collide on
    shared ``user_id`` keys.
    """

    # user_id -> asyncio.Queue[MessageTask]
    queues: dict[int, Any] = field(default_factory=dict)
    # user_id -> asyncio.Task
    workers: dict[int, Any] = field(default_factory=dict)
    # user_id -> asyncio.Lock
    locks: dict[int, Any] = field(default_factory=dict)
    # (tool_use_id, user_id, thread_id_or_0) -> telegram message_id
    tool_msg_ids: dict[tuple[str, int, int], int] = field(default_factory=dict)
    # (user_id, thread_id_or_0) -> (message_id, window_id, last_text)
    status_msg_info: dict[tuple[int, int], tuple[int, str, str]] = field(
        default_factory=dict
    )


@dataclass
class InteractiveUIState:
    """Per-agent interactive UI state.

    Avoids module-level globals so multiple agents don't collide.
    """

    # (user_id, thread_id_or_0) -> telegram message_id
    msgs: dict[tuple[int, int], int] = field(default_factory=dict)
    # (user_id, thread_id_or_0) -> window_id
    mode: dict[tuple[int, int], str] = field(default_factory=dict)


@dataclass
class AgentContext:
    """Runtime context for a single agent."""

    config: AgentConfig
    tmux_manager: TmuxManager
    session_manager: SessionManager
    session_monitor: SessionMonitor | None = None
    cron_service: CronService | None = None

    # Per-agent handler state (isolated per agent for multi-agent support)
    queue_state: MessageQueueState = field(default_factory=MessageQueueState)
    ui_state: InteractiveUIState = field(default_factory=InteractiveUIState)


def create_agent_context(config: AgentConfig) -> AgentContext:
    """Build an AgentContext from an AgentConfig.

    Creates TmuxManager, SessionManager, and CronService instances
    wired to the agent's config.  SessionMonitor is left as None
    (created later during bot startup when the Application is available).
    """
    from .cron.service import CronService
    from .session import SessionManager
    from .tmux_manager import TmuxManager

    tmux_mgr = TmuxManager(
        session_name=config.tmux_session_name,
        claude_command=config.claude_command,
        main_window_name=config.tmux_main_window_name,
    )

    session_mgr = SessionManager(
        state_file=config.state_file,
        session_map_file=config.session_map_file,
        tmux_session_name=config.tmux_session_name,
        claude_projects_path=config.claude_projects_path,
        tmux_manager=tmux_mgr,
    )

    cron_svc = CronService(
        session_manager=session_mgr,
        tmux_manager=tmux_mgr,
        cron_default_tz=config.cron_default_tz,
        users_dir=config.users_dir,
        workspace_dir_for=config.workspace_dir_for,
        iter_workspace_dirs=config.iter_workspace_dirs,
    )

    return AgentContext(
        config=config,
        tmux_manager=tmux_mgr,
        session_manager=session_mgr,
        cron_service=cron_svc,
    )
