"""ForumRouter — routes messages via Telegram Forum topics.

Each topic (thread_id) maps to one tmux window. This is the original
routing mode that was previously hardcoded in bot.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.ext import MessageHandler, filters

from ..router import Router, RoutingKey

if TYPE_CHECKING:
    from telegram import Bot
    from telegram.ext import Application, ContextTypes

    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)


class ForumRouter(Router):
    """Forum-mode router: 1 topic = 1 window = 1 session."""

    def extract_routing_key(self, update: Update) -> RoutingKey | None:
        msg = update.message or (
            update.callback_query.message if update.callback_query else None
        )
        if msg is None:
            return None
        user = update.effective_user
        if user is None:
            return None
        tid = getattr(msg, "message_thread_id", None)
        # Exclude General topic (tid == 1) and non-topic messages
        if tid is None or tid == 1:
            return None
        return RoutingKey(
            user_id=user.id,
            chat_id=msg.chat.id,
            session_key=tid,
            thread_id=tid,
        )

    def rejection_message(self) -> str:
        return "Please use a named topic. Create a new topic to start a session."

    def workspace_name(self, rk: RoutingKey, ctx: AgentContext) -> str:
        assert rk.thread_id is not None
        return (
            ctx.session_manager.get_topic_name(rk.thread_id) or f"topic-{rk.thread_id}"
        )

    def get_window(self, rk: RoutingKey, ctx: AgentContext) -> str | None:
        # Check this user's binding first
        wid = ctx.session_manager.get_window_for_thread(rk.user_id, rk.session_key)
        if wid:
            return wid
        # Fallback: another user may already have a window for this topic — share it
        for other_uid, other_tid, other_wid in ctx.session_manager.iter_thread_bindings():
            if other_tid == rk.session_key and other_uid != rk.user_id:
                # Auto-bind this user to the same window
                display = ctx.session_manager.get_display_name(other_wid)
                ctx.session_manager.bind_thread(
                    rk.user_id, rk.session_key, other_wid, window_name=display
                )
                logger.info(
                    "Auto-bound user %d to existing window %s for thread %d",
                    rk.user_id, other_wid, rk.session_key,
                )
                return other_wid
        return None

    def bind_window(
        self,
        rk: RoutingKey,
        window_id: str,
        window_name: str,
        ctx: AgentContext,
    ) -> None:
        ctx.session_manager.bind_thread(
            rk.user_id, rk.session_key, window_id, window_name=window_name
        )

    def unbind_window(self, rk: RoutingKey, ctx: AgentContext) -> str | None:
        return ctx.session_manager.unbind_thread(rk.user_id, rk.session_key)

    def store_chat_context(self, rk: RoutingKey, ctx: AgentContext) -> None:
        # In forum mode, store group chat_id for message delivery
        ctx.session_manager.set_group_chat_id(rk.user_id, rk.session_key, rk.chat_id)

    def resolve_chat_id(self, rk: RoutingKey, ctx: AgentContext) -> int:
        return ctx.session_manager.resolve_chat_id(rk.user_id, rk.thread_id)

    def send_kwargs(self, rk: RoutingKey) -> dict[str, Any]:
        return {"message_thread_id": rk.thread_id}

    def iter_bindings(self, ctx: AgentContext) -> list[tuple[RoutingKey, str]]:
        result: list[tuple[RoutingKey, str]] = []
        for user_id, thread_id, window_id in ctx.session_manager.iter_thread_bindings():
            chat_id = ctx.session_manager.resolve_chat_id(user_id, thread_id)
            rk = RoutingKey(
                user_id=user_id,
                chat_id=chat_id,
                session_key=thread_id,
                thread_id=thread_id,
            )
            result.append((rk, window_id))
        return result

    def register_lifecycle_handlers(self, application: Application) -> None:
        application.add_handler(
            MessageHandler(
                filters.StatusUpdate.FORUM_TOPIC_CREATED,
                _topic_created_handler,
            )
        )
        application.add_handler(
            MessageHandler(
                filters.StatusUpdate.FORUM_TOPIC_CLOSED,
                _topic_closed_handler,
            )
        )

    async def probe_binding_exists(
        self, rk: RoutingKey, bot: Bot, ctx: AgentContext
    ) -> bool:
        """Probe topic existence via unpin_all_forum_topic_messages."""
        from telegram.error import BadRequest

        try:
            await bot.unpin_all_forum_topic_messages(
                chat_id=self.resolve_chat_id(rk, ctx),
                message_thread_id=rk.session_key,
            )
            return True
        except BadRequest as e:
            if "Topic_id_invalid" in str(e):
                return False
            # Other errors (permissions, etc.) — assume topic still exists
            return True
        except Exception:
            return True


# --- Forum lifecycle handlers ---


async def _topic_created_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Persist topic name when a new topic is created."""
    if not update.message or not update.message.forum_topic_created:
        return
    ftc = update.message.forum_topic_created
    thread_id = update.message.message_thread_id
    if thread_id and ftc.name:
        ctx: AgentContext = context.bot_data["agent_ctx"]
        ctx.session_manager.set_topic_name(thread_id, ftc.name)
        logger.debug("Persisted topic name: thread=%d, name=%s", thread_id, ftc.name)


async def _topic_closed_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle topic closure — kill the associated tmux window and clean up state."""
    from ..handlers.cleanup import clear_topic_state
    from ..handlers.status_polling import clear_window_health

    user = update.effective_user
    if not user:
        return

    ctx: AgentContext = context.bot_data["agent_ctx"]
    if not ctx.config.is_user_allowed(user.id):
        return

    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return

    wid = ctx.session_manager.get_window_for_thread(user.id, tid)
    if wid:
        display = ctx.session_manager.get_display_name(wid)
        w = await ctx.tmux_manager.find_window_by_id(wid)
        if w:
            await ctx.tmux_manager.kill_window(w.window_id)
            logger.info(
                "Topic closed: killed window %s (user=%d, thread=%d)",
                display,
                user.id,
                tid,
            )
        else:
            logger.info(
                "Topic closed: window %s already gone (user=%d, thread=%d)",
                display,
                user.id,
                tid,
            )
        clear_window_health(wid)
        ctx.session_manager.unbind_thread(user.id, tid)
        await clear_topic_state(
            user.id, tid, context.bot, context.user_data, agent_ctx=ctx
        )
    else:
        logger.debug("Topic closed: no binding (user=%d, thread=%d)", user.id, tid)
