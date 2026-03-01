"""Telegram handler for /verbosity command and shared filter logic.

Allows per-user control of message display verbosity:
  - quiet:   Only final assistant replies + interactive UI
  - normal:  Assistant replies + tool_use summaries + interactive UI (default)
  - verbose: Everything (thinking, tool_result, user echo, etc.)

Key functions: should_skip_message(), verbosity_command(), handle_verbosity_callback().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .callback_data import CB_VERBOSITY
from .message_sender import safe_edit, safe_reply

if TYPE_CHECKING:
    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)


def should_skip_message(content_type: str, role: str, verbosity: str) -> bool:
    """Check if a message should be skipped based on verbosity level.

    Interactive tools are NEVER skipped (callers must handle that before calling).

    Args:
        content_type: "text", "thinking", "tool_use", "tool_result", "local_command".
        role: "user" or "assistant".
        verbosity: "quiet", "normal", or "verbose".
    """
    if verbosity == "verbose":
        return False

    if verbosity == "quiet":
        # Only pass assistant text
        return not (role == "assistant" and content_type == "text")

    # normal: pass assistant text + tool_use
    if role == "assistant" and content_type == "text":
        return False
    if content_type == "tool_use":
        return False
    return True


_LEVEL_DESCRIPTIONS = {
    "quiet": "only final replies",
    "normal": "replies + tool summaries",
    "verbose": "show everything",
}


def _build_verbosity_keyboard(current: str, thread_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard with verbosity level buttons."""
    buttons = []
    for level, desc in _LEVEL_DESCRIPTIONS.items():
        check = " \u2705" if level == current else ""
        buttons.append(
            InlineKeyboardButton(
                f"{level}{check}",
                callback_data=f"{CB_VERBOSITY}{thread_id}:{level}",
            )
        )
    return InlineKeyboardMarkup([buttons])


def _build_verbosity_text(current: str) -> str:
    """Build the message text showing current verbosity setting."""
    desc = _LEVEL_DESCRIPTIONS.get(current, "")
    return f"\U0001f4ca Display mode: {current} ({desc})"


async def verbosity_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /verbosity — show current setting with inline keyboard."""
    user = update.effective_user
    if not user or not update.message:
        return

    agent_ctx: AgentContext = context.bot_data["agent_ctx"]
    if not agent_ctx.config.is_user_allowed(user.id):
        return

    thread_id = update.message.message_thread_id or 0
    # In group mode, use chat_id as verbosity key (matches _deliver_message queue_id)
    chat = update.effective_chat
    if agent_ctx.config.mode == "group" and chat:
        vkey = chat.id
    else:
        vkey = user.id
    current = agent_ctx.session_manager.get_verbosity(vkey, thread_id)
    text = _build_verbosity_text(current)
    keyboard = _build_verbosity_keyboard(current, thread_id)
    await safe_reply(update.message, text, reply_markup=keyboard)


async def handle_verbosity_callback(
    query: CallbackQuery, agent_ctx: AgentContext
) -> None:
    """Handle verbosity inline keyboard callback.

    Args:
        query: CallbackQuery with data starting with CB_VERBOSITY.
        agent_ctx: AgentContext for accessing session manager.
    """
    user = query.from_user
    if not user:
        await query.answer("Unknown user")
        return

    data = query.data or ""
    payload = data[len(CB_VERBOSITY) :]
    # Format: "<thread_id>:<level>"
    if ":" not in payload:
        await query.answer("Invalid data")
        return
    tid_str, level = payload.split(":", 1)
    try:
        thread_id = int(tid_str)
    except ValueError:
        await query.answer("Invalid data")
        return
    if level not in _LEVEL_DESCRIPTIONS:
        await query.answer("Invalid level")
        return

    # In group mode, use chat_id as verbosity key (matches _deliver_message queue_id)
    chat = query.message.chat if query.message else None
    if agent_ctx.config.mode == "group" and chat:
        vkey = chat.id
    else:
        vkey = user.id
    agent_ctx.session_manager.set_verbosity(vkey, thread_id, level)
    text = _build_verbosity_text(level)
    keyboard = _build_verbosity_keyboard(level, thread_id)

    try:
        await safe_edit(query, text, reply_markup=keyboard)
    except Exception:
        pass
    await query.answer(f"Set to {level}")
