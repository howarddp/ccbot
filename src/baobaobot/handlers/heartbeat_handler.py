"""Telegram handler for /heartbeat command — toggle heartbeat per workspace.

Allows per-workspace control of the heartbeat mechanism:
  - ON:  SystemScheduler checks HEARTBEAT.md every 30 min (default)
  - OFF: Heartbeat checks disabled for this workspace

Key functions: heartbeat_command(), handle_heartbeat_callback().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .callback_data import CB_HEARTBEAT
from .message_sender import safe_edit, safe_reply
from .workspace_resolver import resolve_workspace_for_window

if TYPE_CHECKING:
    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)


def _build_heartbeat_keyboard(enabled: bool, wid: str) -> InlineKeyboardMarkup:
    """Build inline keyboard with ON/OFF toggle and Trigger button."""
    on_label = "ON \u2705" if enabled else "ON"
    off_label = "OFF \u2705" if not enabled else "OFF"
    rows = [
        [
            InlineKeyboardButton(
                on_label,
                callback_data=f"{CB_HEARTBEAT}{wid}:on",
            ),
            InlineKeyboardButton(
                off_label,
                callback_data=f"{CB_HEARTBEAT}{wid}:off",
            ),
        ],
    ]
    if enabled:
        rows.append([
            InlineKeyboardButton(
                "\u26a1 Trigger Now",
                callback_data=f"{CB_HEARTBEAT}{wid}:trigger",
            ),
        ])
    return InlineKeyboardMarkup(rows)


def _build_heartbeat_text(
    enabled: bool, ws_name: str, item_count: tuple[int, int]
) -> str:
    """Build the message text showing heartbeat status."""
    hb_items, todo_count = item_count
    status = "ON" if enabled else "OFF"
    return (
        f"\U0001f493 *Heartbeat*: {status}\n"
        f"Workspace: {ws_name}\n"
        f"HEARTBEAT\\.md items: {hb_items}\n"
        f"Open TODOs: {todo_count}"
    )


async def heartbeat_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /heartbeat — show current setting with inline keyboard."""
    user = update.effective_user
    if not user or not update.message:
        return

    agent_ctx: AgentContext = context.bot_data["agent_ctx"]
    if not agent_ctx.config.is_user_allowed(user.id):
        return

    rk = agent_ctx.router.extract_routing_key(update)
    if rk is None:
        await safe_reply(update.message, "\u274c No workspace for this topic.")
        return
    wid = agent_ctx.router.get_window(rk, agent_ctx)
    if not wid:
        await safe_reply(update.message, "\u274c No session bound to this topic.")
        return

    ws_dir = resolve_workspace_for_window(agent_ctx, wid)
    if not ws_dir:
        await safe_reply(update.message, "\u274c Cannot resolve workspace.")
        return

    scheduler = agent_ctx.system_scheduler
    if not scheduler:
        await safe_reply(update.message, "\u274c System scheduler not available.")
        return

    display = agent_ctx.session_manager.get_display_name(wid)
    agent_prefix = f"{agent_ctx.config.name}/"
    ws_name = display.removeprefix(agent_prefix)

    enabled = scheduler.get_heartbeat_enabled(ws_dir)
    item_count = scheduler.get_heartbeat_item_count(ws_dir)
    text = _build_heartbeat_text(enabled, ws_name, item_count)
    keyboard = _build_heartbeat_keyboard(enabled, wid)
    await safe_reply(update.message, text, reply_markup=keyboard)


async def handle_heartbeat_callback(
    query: CallbackQuery, agent_ctx: AgentContext
) -> None:
    """Handle heartbeat inline keyboard callback.

    Args:
        query: CallbackQuery with data starting with CB_HEARTBEAT.
        agent_ctx: AgentContext for accessing system scheduler.
    """
    user = query.from_user
    if not user:
        await query.answer("Unknown user")
        return

    data = query.data or ""
    payload = data[len(CB_HEARTBEAT):]
    # Format: "<window_id>:<on|off>"
    if ":" not in payload:
        await query.answer("Invalid data")
        return
    wid, action = payload.rsplit(":", 1)
    if action not in ("on", "off", "trigger"):
        await query.answer("Invalid action")
        return

    ws_dir = resolve_workspace_for_window(agent_ctx, wid)
    if not ws_dir:
        await query.answer("Cannot resolve workspace", show_alert=True)
        return

    scheduler = agent_ctx.system_scheduler
    if not scheduler:
        await query.answer("Scheduler not available", show_alert=True)
        return

    display = agent_ctx.session_manager.get_display_name(wid)
    agent_prefix = f"{agent_ctx.config.name}/"
    ws_name = display.removeprefix(agent_prefix)

    if action == "trigger":
        sent = await scheduler.trigger_heartbeat(ws_name)
        await query.answer(
            "Heartbeat triggered!" if sent else "No items in HEARTBEAT.md",
            show_alert=not sent,
        )
        # Refresh panel to show updated state
        enabled = scheduler.get_heartbeat_enabled(ws_dir)
        item_count = scheduler.get_heartbeat_item_count(ws_dir)
        text = _build_heartbeat_text(enabled, ws_name, item_count)
        keyboard = _build_heartbeat_keyboard(enabled, wid)
        try:
            await safe_edit(query, text, reply_markup=keyboard)
        except Exception:
            pass
        return

    enabled = action == "on"
    scheduler.set_heartbeat_enabled(ws_dir, enabled)

    item_count = scheduler.get_heartbeat_item_count(ws_dir)
    text = _build_heartbeat_text(enabled, ws_name, item_count)
    keyboard = _build_heartbeat_keyboard(enabled, wid)

    try:
        await safe_edit(query, text, reply_markup=keyboard)
    except Exception:
        pass
    await query.answer(f"Heartbeat {'ON' if enabled else 'OFF'}")
