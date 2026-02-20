"""Router abstraction — decouples message routing from platform specifics.

Defines the RoutingKey (identifies where a message should be routed)
and the Router ABC (how to route messages in different modes like
forum topics vs. group chats).

Concrete implementations: ForumRouter, GroupRouter (in routers/).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telegram import Bot, Update
    from telegram.ext import Application

    from .agent_context import AgentContext


@dataclass(frozen=True)
class RoutingKey:
    """Identifies a routing destination for a message.

    Attributes:
        user_id: Telegram user ID of the sender.
        chat_id: Telegram chat ID (group or user).
        session_key: The key used for session routing.
            Forum mode: thread_id.  Group mode: chat_id.
        thread_id: message_thread_id for replies (None in group mode).
    """

    user_id: int
    chat_id: int
    session_key: int
    thread_id: int | None


class Router(abc.ABC):
    """Abstract router — encapsulates forum vs group routing logic."""

    @abc.abstractmethod
    def extract_routing_key(self, update: Update) -> RoutingKey | None:
        """Extract a RoutingKey from an incoming Update.

        Returns None if the update cannot be routed (e.g. General topic).
        """

    @abc.abstractmethod
    def rejection_message(self) -> str:
        """Message to show when extract_routing_key returns None."""

    @abc.abstractmethod
    def workspace_name(self, rk: RoutingKey, ctx: AgentContext) -> str:
        """Derive the workspace name for a routing key."""

    @abc.abstractmethod
    def get_window(self, rk: RoutingKey, ctx: AgentContext) -> str | None:
        """Look up the bound window_id for a routing key."""

    @abc.abstractmethod
    def bind_window(
        self,
        rk: RoutingKey,
        window_id: str,
        window_name: str,
        ctx: AgentContext,
    ) -> None:
        """Bind a routing key to a tmux window."""

    @abc.abstractmethod
    def unbind_window(self, rk: RoutingKey, ctx: AgentContext) -> str | None:
        """Unbind a routing key. Returns the previously bound window_id."""

    @abc.abstractmethod
    def store_chat_context(self, rk: RoutingKey, ctx: AgentContext) -> None:
        """Persist any chat context needed for message delivery."""

    @abc.abstractmethod
    def resolve_chat_id(self, rk: RoutingKey, ctx: AgentContext) -> int:
        """Resolve the Telegram chat_id for sending messages."""

    @abc.abstractmethod
    def send_kwargs(self, rk: RoutingKey) -> dict[str, Any]:
        """Extra kwargs for bot.send_message (e.g. message_thread_id)."""

    @abc.abstractmethod
    def iter_bindings(self, ctx: AgentContext) -> list[tuple[RoutingKey, str]]:
        """Return all active (RoutingKey, window_id) bindings."""

    @abc.abstractmethod
    def register_lifecycle_handlers(self, application: Application) -> None:
        """Register platform-specific lifecycle handlers (e.g. topic events)."""

    @abc.abstractmethod
    async def probe_binding_exists(
        self, rk: RoutingKey, bot: Bot, ctx: AgentContext
    ) -> bool:
        """Probe whether the binding target still exists.

        Forum: checks topic existence via unpin API.
        Group: always True.
        """
