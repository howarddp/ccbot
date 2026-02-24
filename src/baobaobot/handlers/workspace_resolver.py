"""Shared workspace directory resolution for Telegram handlers.

Provides rename-safe workspace resolution by preferring the actual cwd
from the session map (WindowState.cwd) over computing from the display
name.  This protects against the group/topic rename problem: even if the
chat title changes, the original workspace directory is still found.

Key functions:
    resolve_workspace_for_window   — given agent_ctx + window_id → Path
    resolve_workspace_for_update   — given update + context → Path
"""

from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes


def resolve_workspace_for_window(agent_ctx, wid: str) -> Path | None:  # type: ignore[no-untyped-def]
    """Resolve the workspace directory for a tmux window.

    Strategy (in priority order):
      1. WindowState.cwd — populated from session_map.json by the hook.
         Authoritative and rename-safe: survives group/topic title changes.
      2. Display name fallback — strips the agent prefix and derives the
         workspace path from the name via workspace_dir_for().

    Returns None if neither strategy yields a result.
    """
    state = agent_ctx.session_manager.get_window_state(wid)
    if state.cwd:
        cwd_path = Path(state.cwd)
        if cwd_path.is_dir():
            return cwd_path

    # Fallback: derive from display name (strips agent prefix)
    display_name = agent_ctx.session_manager.get_display_name(wid)
    agent_prefix = f"{agent_ctx.config.name}/"
    ws_name = display_name.removeprefix(agent_prefix)
    return agent_ctx.config.workspace_dir_for(ws_name)


def resolve_workspace_for_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Path | None:
    """Resolve the workspace directory for the current routing context.

    Extracts the routing key, looks up the bound window, then delegates to
    resolve_workspace_for_window().

    Returns None if no window is bound or no workspace can be determined.
    """
    agent_ctx = context.bot_data["agent_ctx"]
    rk = agent_ctx.router.extract_routing_key(update)
    if rk is None:
        return None

    wid = agent_ctx.router.get_window(rk, agent_ctx)
    if not wid:
        return None

    return resolve_workspace_for_window(agent_ctx, wid)
