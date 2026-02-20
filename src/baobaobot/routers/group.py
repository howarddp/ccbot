"""GroupRouter — routes messages via Telegram group chats.

Each group chat (chat_id) maps to one tmux window. All allowed_users
in the same group share the same session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..router import Router, RoutingKey

if TYPE_CHECKING:
    from telegram import Bot, Update
    from telegram.ext import Application

    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)


class GroupRouter(Router):
    """Group-mode router: 1 group chat = 1 window = 1 session."""

    def extract_routing_key(self, update: Update) -> RoutingKey | None:
        msg = update.message or (
            update.callback_query.message if update.callback_query else None
        )
        if msg is None:
            return None
        user = update.effective_user
        if user is None:
            return None
        chat = msg.chat
        if chat.type not in ("group", "supergroup"):
            return None
        # Reject forum supergroups — those belong to ForumRouter
        if getattr(chat, "is_forum", False):
            return None
        return RoutingKey(
            user_id=user.id,
            chat_id=chat.id,
            session_key=chat.id,
            thread_id=None,
        )

    def rejection_message(self) -> str:
        return "This bot only works in group chats."

    def workspace_name(self, rk: RoutingKey, ctx: AgentContext) -> str:
        return ctx.session_manager.get_group_title(rk.chat_id) or f"group-{rk.chat_id}"

    def get_window(self, rk: RoutingKey, ctx: AgentContext) -> str | None:
        return ctx.session_manager.get_window_for_group(rk.chat_id)

    def bind_window(
        self,
        rk: RoutingKey,
        window_id: str,
        window_name: str,
        ctx: AgentContext,
    ) -> None:
        ctx.session_manager.bind_group(rk.chat_id, window_id, window_name)

    def unbind_window(self, rk: RoutingKey, ctx: AgentContext) -> str | None:
        return ctx.session_manager.unbind_group(rk.chat_id)

    def store_chat_context(self, rk: RoutingKey, ctx: AgentContext) -> None:
        # In group mode, store the chat title if available
        pass

    def resolve_chat_id(self, rk: RoutingKey, ctx: AgentContext) -> int:
        return rk.chat_id

    def send_kwargs(self, rk: RoutingKey) -> dict[str, Any]:
        return {}

    def iter_bindings(self, ctx: AgentContext) -> list[tuple[RoutingKey, str]]:
        result: list[tuple[RoutingKey, str]] = []
        for chat_id, window_id in ctx.session_manager.iter_group_bindings():
            rk = RoutingKey(
                user_id=0,  # group bindings are not per-user
                chat_id=chat_id,
                session_key=chat_id,
                thread_id=None,
            )
            result.append((rk, window_id))
        return result

    def register_lifecycle_handlers(self, application: Application) -> None:
        # No lifecycle handlers needed for group mode
        pass

    async def probe_binding_exists(
        self, rk: RoutingKey, bot: Bot, ctx: AgentContext
    ) -> bool:
        # Group chats don't need probing — always considered alive
        return True
