"""Message history display with pagination.

Provides history viewing functionality for Claude Code sessions:
  - _build_history_keyboard: Build inline keyboard for page navigation
  - send_history: Send or edit message history with pagination support

Supports both full history and unread message range views.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ..telegram_sender import split_message
from ..transcript_parser import TranscriptParser
from .callback_data import CB_HISTORY_NEXT, CB_HISTORY_PREV
from .message_sender import safe_edit, safe_reply, safe_send
from .verbosity_handler import should_skip_message

if TYPE_CHECKING:
    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)


def _build_history_keyboard(
    window_id: str,
    page_index: int,
    total_pages: int,
    start_byte: int = 0,
    end_byte: int = 0,
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for history pagination.

    Callback format: hp:<page>:<window_id>:<start>:<end> or hn:<page>:<window_id>:<start>:<end>
    When start=0 and end=0, it means full history (no byte range filter).
    """
    if total_pages <= 1:
        return None

    buttons = []
    if page_index > 0:
        cb_data = (
            f"{CB_HISTORY_PREV}{page_index - 1}:{window_id}:{start_byte}:{end_byte}"
        )
        buttons.append(
            InlineKeyboardButton(
                "◀ Older",
                callback_data=cb_data[:64],
            )
        )

    buttons.append(
        InlineKeyboardButton(f"{page_index + 1}/{total_pages}", callback_data="noop")
    )

    if page_index < total_pages - 1:
        cb_data = (
            f"{CB_HISTORY_NEXT}{page_index + 1}:{window_id}:{start_byte}:{end_byte}"
        )
        buttons.append(
            InlineKeyboardButton(
                "Newer ▶",
                callback_data=cb_data[:64],
            )
        )

    return InlineKeyboardMarkup([buttons])


async def send_history(
    target: Any,
    window_id: str,
    offset: int = -1,
    edit: bool = False,
    *,
    start_byte: int = 0,
    end_byte: int = 0,
    user_id: int | None = None,
    bot: Bot | None = None,
    message_thread_id: int | None = None,
    agent_ctx: AgentContext,
) -> None:
    """Send or edit message history for a window's session.

    Args:
        target: Message object (for reply) or CallbackQuery (for edit).
        window_id: Tmux window ID (resolved to session via window_states).
        offset: Page index (0-based). -1 means last page (for full history)
                or first page (for unread range).
        edit: If True, edit existing message instead of sending new one.
        start_byte: Start byte offset (0 = from beginning).
        end_byte: End byte offset (0 = to end of file).
        user_id: User ID for updating read offset (required for unread mode).
        bot: Bot instance for direct send mode (when edit=False and bot is provided).
        message_thread_id: Telegram topic thread_id for targeted send.
        agent_ctx: AgentContext for accessing services.
    """
    sm = agent_ctx.session_manager
    # Determine verbosity key: in group mode use chat_id, otherwise user_id
    _hist_vkey = user_id
    if _hist_vkey is None:
        _hist_user = getattr(target, "from_user", None)
        if _hist_user:
            _hist_vkey = _hist_user.id
    # In group mode, override with chat_id to match _deliver_message queue_id
    if agent_ctx.config.mode == "group":
        _hist_chat = getattr(target, "chat", None)
        if _hist_chat:
            _hist_vkey = _hist_chat.id
    verbosity = (
        sm.get_verbosity(_hist_vkey, message_thread_id or 0)
        if _hist_vkey
        else "normal"
    )

    display_name = sm.get_display_name(window_id)
    # Determine if this is unread mode (specific byte range)
    is_unread = start_byte > 0 or end_byte > 0
    logger.debug(
        "send_history: window_id=%s (%s), offset=%d, is_unread=%s, byte_range=%d-%d",
        window_id,
        display_name,
        offset,
        is_unread,
        start_byte,
        end_byte,
    )

    messages, total = await sm.get_recent_messages(
        window_id,
        start_byte=start_byte,
        end_byte=end_byte if end_byte > 0 else None,
    )

    if total == 0:
        if is_unread:
            text = f"📬 [{display_name}] No unread messages."
        else:
            text = f"📋 [{display_name}] No messages yet."
        keyboard = None
    else:
        _start = TranscriptParser.EXPANDABLE_QUOTE_START
        _end = TranscriptParser.EXPANDABLE_QUOTE_END

        # Filter messages based on per-user verbosity
        if verbosity != "verbose":
            messages = [
                m
                for m in messages
                if not should_skip_message(
                    m.get("content_type", "text"), m.get("role", "assistant"), verbosity
                )
            ]
        total = len(messages)
        if total == 0:
            if is_unread:
                text = f"📬 [{display_name}] No unread messages."
            else:
                text = f"📋 [{display_name}] No messages yet."
            keyboard = None
            if edit:
                await safe_edit(target, text, reply_markup=keyboard)
            elif bot is not None and user_id is not None:
                await safe_send(
                    bot,
                    sm.resolve_chat_id(user_id, message_thread_id),
                    text,
                    message_thread_id=message_thread_id,
                    reply_markup=keyboard,
                )
            else:
                await safe_reply(target, text, reply_markup=keyboard)
            # Update offset even if no assistant messages
            if user_id is not None and end_byte > 0:
                sm.update_user_window_offset(user_id, window_id, end_byte)
            return

        if is_unread:
            header = f"📬 [{display_name}] {total} unread messages"
        else:
            header = f"📋 [{display_name}] Messages ({total} total)"

        lines = [header]
        for msg in messages:
            # Format timestamp as HH:MM in local time
            ts = msg.get("timestamp")
            if ts:
                try:
                    # ISO format: 2024-01-15T14:32:00.000Z → local time
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    local_dt = dt.astimezone()
                    hh_mm = local_dt.strftime("%H:%M")
                except (ValueError, TypeError):
                    hh_mm = ""
            else:
                hh_mm = ""

            # Add separator with time
            if hh_mm:
                lines.append(f"───── {hh_mm} ─────")
            else:
                lines.append("─────────────")

            # Format message content
            msg_text = msg["text"]
            content_type = msg.get("content_type", "text")
            msg_role = msg.get("role", "assistant")

            # Strip expandable quote sentinels for history view
            msg_text = msg_text.replace(_start, "").replace(_end, "")

            # Add prefix based on role/type
            if msg_role == "user":
                # User message with emoji prefix (no newline)
                lines.append(f"👤 {msg_text}")
            elif content_type == "thinking":
                # Thinking prefix to match real-time format
                lines.append(f"∴ Thinking…\n{msg_text}")
            else:
                lines.append(msg_text)
        full_text = "\n\n".join(lines)
        pages = split_message(full_text, max_length=4096)

        # Default to last page (newest messages) for both history and unread
        if offset < 0:
            offset = len(pages) - 1
        page_index = max(0, min(offset, len(pages) - 1))
        text = pages[page_index]
        keyboard = _build_history_keyboard(
            window_id, page_index, len(pages), start_byte, end_byte
        )
        logger.debug(
            "send_history result: %d messages, %d pages, serving page %d",
            total,
            len(pages),
            page_index,
        )

    if edit:
        await safe_edit(target, text, reply_markup=keyboard)
    elif bot is not None and user_id is not None:
        # Direct send mode (for unread catch-up after window switch)
        await safe_send(
            bot,
            sm.resolve_chat_id(user_id, message_thread_id),
            text,
            message_thread_id=message_thread_id,
            reply_markup=keyboard,
        )
    else:
        await safe_reply(target, text, reply_markup=keyboard)

    # Update user's read offset after viewing unread
    if is_unread and user_id is not None and end_byte > 0:
        sm.update_user_window_offset(user_id, window_id, end_byte)
