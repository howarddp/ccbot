"""Unified cleanup API for topic and user state.

Provides centralized cleanup functions that coordinate state cleanup across
all modules, preventing memory leaks when topics are deleted or users disconnect.

Functions:
  - clear_topic_state: Clean up all memory state for a specific topic
  - clear_user_state: Clean up all memory state for a user
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import Bot

from .interactive_ui import clear_interactive_msg
from .message_queue import clear_status_msg_info, clear_tool_msg_ids_for_topic

if TYPE_CHECKING:
    from ..agent_context import AgentContext


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    bot: Bot | None = None,
    user_data: dict[str, Any] | None = None,
    *,
    agent_ctx: AgentContext,
) -> None:
    """Clear all memory state associated with a topic.

    This should be called when:
      - A topic is closed or deleted
      - A thread binding becomes stale (window deleted externally)

    Cleans up:
      - _status_msg_info (status message tracking)
      - _tool_msg_ids (tool_use -> message_id mapping)
      - _interactive_msgs and _interactive_mode (interactive UI state)
      - user_data pending state (_pending_thread_id, _pending_thread_text)
    """
    # Clear status message tracking
    clear_status_msg_info(agent_ctx, user_id, thread_id)

    # Clear tool message ID tracking
    clear_tool_msg_ids_for_topic(agent_ctx, user_id, thread_id)

    # Clear interactive UI state (also deletes message from chat)
    await clear_interactive_msg(user_id, bot, thread_id, agent_ctx=agent_ctx)

    # Clear pending thread state from user_data
    if user_data is not None:
        if user_data.get("_pending_thread_id") == thread_id:
            user_data.pop("_pending_thread_id", None)
            user_data.pop("_pending_thread_text", None)


async def clear_user_state(
    user_id: int,
    bot: Bot | None = None,
    user_data: dict[str, Any] | None = None,
    *,
    agent_ctx: AgentContext,
) -> None:
    """Clear all memory state associated with a user.

    This should be called when a user fully disconnects or is removed.

    Cleans up all topics for the user via clear_topic_state.
    """
    sm = agent_ctx.session_manager

    # Get all thread bindings for this user and clean up each
    bindings = sm.get_all_thread_windows(user_id)
    for thread_id in bindings:
        await clear_topic_state(user_id, thread_id, bot, user_data, agent_ctx=agent_ctx)
