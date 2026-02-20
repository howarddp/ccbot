"""Terminal status line polling for thread-bound windows.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram
  - Polls thread_bindings (each topic = one window)
  - Periodically probes topic existence via unpin_all_forum_topic_messages
    (silent no-op when no pins); cleans up deleted topics (kills tmux window
    + unbinds thread)

Key components:
  - STATUS_POLL_INTERVAL: Polling frequency (1 second)
  - TOPIC_CHECK_INTERVAL: Topic existence probe frequency (60 seconds)
  - status_poll_loop: Background polling task
  - update_status_message: Poll and enqueue status updates
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ..terminal_parser import is_interactive_ui, parse_status_line
from .callback_data import CB_RESTART_SESSION
from .cleanup import clear_topic_state
from .interactive_ui import (
    clear_interactive_msg,
    get_interactive_window,
    handle_interactive_ui,
)
from .message_queue import enqueue_status_update, get_message_queue
from .message_sender import rate_limit_send_message

if TYPE_CHECKING:
    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)

# Status polling interval
STATUS_POLL_INTERVAL = 1.0  # seconds - faster response (rate limiting at send layer)

# Topic existence probe interval
TOPIC_CHECK_INTERVAL = 60.0  # seconds

# Freeze detection
FREEZE_TIMEOUT = 60.0  # seconds of unchanged pane + stale spinner → freeze

# Shutdown guard — prevents destructive cleanup during bot shutdown
_shutting_down = False


@dataclass
class _WindowHealth:
    """Per-window health tracking for freeze detection."""

    last_pane_hash: str = ""
    unchanged_since: float = 0.0
    notified: bool = False


_window_health: dict[str, _WindowHealth] = {}


def signal_shutdown() -> None:
    """Signal that the bot is shutting down.

    Once set, the poll loop skips all destructive operations (unbinding
    threads, killing windows) to avoid corrupting state during restart.
    """
    global _shutting_down
    _shutting_down = True


def clear_window_health(window_id: str) -> None:
    """Reset health tracking for a window (call after restart)."""
    _window_health.pop(window_id, None)


def _check_freeze(
    window_id: str,
    pane_text: str,
) -> bool:
    """Check if a window appears frozen.

    A freeze is detected when:
      1. Pane content is unchanged for FREEZE_TIMEOUT seconds
      2. There is an **active** spinner in the status area (detected by
         ``parse_status_line``) — this excludes stale spinners from old
         output and the ``✻`` in Claude Code's welcome banner.

    Returns True if freeze detected (and not yet notified).
    """
    h = pane_text.encode()
    pane_hash = hashlib.md5(h).hexdigest()

    health = _window_health.get(window_id)
    if health is None:
        health = _WindowHealth()
        _window_health[window_id] = health

    now = time.monotonic()

    if pane_hash != health.last_pane_hash:
        # Content changed — reset
        health.last_pane_hash = pane_hash
        health.unchanged_since = now
        health.notified = False
        return False

    if health.notified:
        return False  # Already sent notification

    elapsed = now - health.unchanged_since
    if elapsed < FREEZE_TIMEOUT:
        return False

    # Only flag as frozen if there's an active spinner in the status area.
    # parse_status_line() scans from bottom up through the last 15 lines and
    # returns None once it hits the ❯ idle prompt — so stale spinners from
    # old output or the Claude Code banner are correctly ignored.
    if parse_status_line(pane_text) is not None:
        health.notified = True
        return True

    return False


async def update_status_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
    *,
    agent_ctx: AgentContext,
) -> None:
    """Poll terminal and enqueue status update for user's active window.

    Also detects permission prompt UIs (not triggered via JSONL) and enters
    interactive mode when found.
    """
    sm = agent_ctx.session_manager
    tm = agent_ctx.tmux_manager

    w = await tm.find_window_by_id(window_id)
    if not w:
        # Window gone, enqueue clear
        await enqueue_status_update(
            bot, user_id, window_id, None, thread_id=thread_id, agent_ctx=agent_ctx
        )
        return

    pane_text = await tm.capture_pane(w.window_id)
    if not pane_text:
        # Transient capture failure - keep existing status message
        return

    interactive_window = get_interactive_window(agent_ctx, user_id, thread_id)
    should_check_new_ui = True

    if interactive_window == window_id:
        # User is in interactive mode for THIS window
        if is_interactive_ui(pane_text):
            # Interactive UI still showing — skip status update (user is interacting)
            return
        # Interactive UI gone — clear interactive mode, fall through to status check.
        # Don't re-check for new UI this cycle (the old one just disappeared).
        await clear_interactive_msg(user_id, bot, thread_id, agent_ctx=agent_ctx)
        should_check_new_ui = False
    elif interactive_window is not None:
        # User is in interactive mode for a DIFFERENT window (window switched)
        # Clear stale interactive mode
        await clear_interactive_msg(user_id, bot, thread_id, agent_ctx=agent_ctx)

    # Check for permission prompt (interactive UI not triggered via JSONL)
    if should_check_new_ui and is_interactive_ui(pane_text):
        await handle_interactive_ui(
            bot, user_id, window_id, thread_id, agent_ctx=agent_ctx
        )
        return

    # Normal status line check
    status_line = parse_status_line(pane_text)

    # Freeze detection: unchanged pane + stale spinner → notify user
    if _check_freeze(window_id, pane_text):
        chat_id = sm.resolve_chat_id(user_id, thread_id)
        display = sm.get_display_name(window_id)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 Restart Session",
                        callback_data=f"{CB_RESTART_SESSION}{window_id}"[:64],
                    )
                ]
            ]
        )
        await rate_limit_send_message(
            bot,
            chat_id,
            f"⚠️ Session *{display}* appears frozen.\n"
            "No activity for 60s. Tap to restart.",
            message_thread_id=thread_id,
            reply_markup=keyboard,
        )
        logger.warning("Freeze detected for window %s (%s)", window_id, display)

    if status_line:
        await enqueue_status_update(
            bot,
            user_id,
            window_id,
            status_line,
            thread_id=thread_id,
            agent_ctx=agent_ctx,
        )
    # If no status line, keep existing status message (don't clear on transient state)


async def status_poll_loop(
    bot: Bot,
    agent_ctx: AgentContext,
) -> None:
    """Background task to poll terminal status for all bound windows."""
    tm = agent_ctx.tmux_manager
    router = agent_ctx.router

    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    last_topic_check = 0.0
    while True:
        try:
            if _shutting_down:
                break

            bindings = router.iter_bindings(agent_ctx)

            # Periodic binding existence probe (e.g. topic deletion for forum mode)
            now = time.monotonic()
            if now - last_topic_check >= TOPIC_CHECK_INTERVAL:
                last_topic_check = now
                for rk, wid in bindings:
                    try:
                        exists = await router.probe_binding_exists(rk, bot, agent_ctx)
                        if not exists:
                            # Binding target gone — kill window, unbind, clean up
                            w = await tm.find_window_by_id(wid)
                            if w:
                                await tm.kill_window(w.window_id)
                            clear_window_health(wid)
                            router.unbind_window(rk, agent_ctx)
                            await clear_topic_state(
                                rk.user_id, rk.session_key, bot, agent_ctx=agent_ctx
                            )
                            logger.info(
                                "Binding target gone: killed window_id '%s' "
                                "(key=%d, user=%d)",
                                wid,
                                rk.session_key,
                                rk.user_id,
                            )
                    except Exception as e:
                        logger.debug(
                            "Binding probe error for %s: %s",
                            wid,
                            e,
                        )

            # Refresh bindings after potential cleanup
            bindings = router.iter_bindings(agent_ctx)

            for rk, wid in bindings:
                try:
                    # Clean up stale bindings (window no longer exists)
                    w = await tm.find_window_by_id(wid)
                    if not w:
                        clear_window_health(wid)
                        router.unbind_window(rk, agent_ctx)
                        await clear_topic_state(
                            rk.user_id, rk.session_key, bot, agent_ctx=agent_ctx
                        )
                        logger.info(
                            "Cleaned up stale binding: key=%d window_id=%s",
                            rk.session_key,
                            wid,
                        )
                        continue

                    # Use user_id as queue key for forum, chat_id for group
                    queue_id = rk.user_id if rk.thread_id is not None else rk.chat_id
                    queue = get_message_queue(agent_ctx, queue_id)
                    if queue and not queue.empty():
                        continue
                    await update_status_message(
                        bot,
                        queue_id,
                        wid,
                        thread_id=rk.thread_id,
                        agent_ctx=agent_ctx,
                    )
                except Exception as e:
                    logger.debug(f"Status update error for key {rk.session_key}: {e}")
        except Exception as e:
            logger.error(f"Status poll loop error: {e}")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
