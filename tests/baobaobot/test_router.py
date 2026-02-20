"""Tests for Router abstraction — ForumRouter, GroupRouter, and factory."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from baobaobot.router import RoutingKey
from baobaobot.routers import create_router
from baobaobot.routers.forum import ForumRouter
from baobaobot.routers.group import GroupRouter


# --- Factory tests ---


class TestCreateRouter:
    def test_forum(self):
        router = create_router("forum")
        assert isinstance(router, ForumRouter)

    def test_group(self):
        router = create_router("group")
        assert isinstance(router, GroupRouter)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown router mode"):
            create_router("discord")


# --- RoutingKey tests ---


class TestRoutingKey:
    def test_frozen(self):
        rk = RoutingKey(user_id=1, chat_id=2, session_key=3, thread_id=3)
        with pytest.raises(AttributeError):
            rk.user_id = 99  # type: ignore[misc]

    def test_fields(self):
        rk = RoutingKey(user_id=10, chat_id=-100, session_key=42, thread_id=42)
        assert rk.user_id == 10
        assert rk.chat_id == -100
        assert rk.session_key == 42
        assert rk.thread_id == 42

    def test_group_mode_no_thread(self):
        rk = RoutingKey(user_id=10, chat_id=-100, session_key=-100, thread_id=None)
        assert rk.thread_id is None
        assert rk.session_key == -100


# --- ForumRouter tests ---


def _make_forum_update(thread_id, user_id=123, chat_id=-1001):
    """Build a minimal Update for forum mode testing."""
    update = MagicMock()
    update.message.message_thread_id = thread_id
    update.message.chat.id = chat_id
    update.message.chat.type = "supergroup"
    update.effective_user.id = user_id
    update.callback_query = None
    return update


def _make_agent_ctx(mode="forum"):
    """Build a minimal AgentContext mock."""
    ctx = MagicMock()
    ctx.config.mode = mode
    ctx.session_manager = MagicMock()
    ctx.tmux_manager = MagicMock()
    return ctx


class TestForumRouter:
    def test_extract_routing_key_valid(self):
        router = ForumRouter()
        update = _make_forum_update(thread_id=42, user_id=123, chat_id=-1001)
        rk = router.extract_routing_key(update)
        assert rk is not None
        assert rk.user_id == 123
        assert rk.chat_id == -1001
        assert rk.session_key == 42
        assert rk.thread_id == 42

    def test_extract_routing_key_general_topic(self):
        router = ForumRouter()
        update = _make_forum_update(thread_id=1)
        rk = router.extract_routing_key(update)
        assert rk is None

    def test_extract_routing_key_no_thread(self):
        router = ForumRouter()
        update = _make_forum_update(thread_id=None)
        rk = router.extract_routing_key(update)
        assert rk is None

    def test_rejection_message(self):
        router = ForumRouter()
        msg = router.rejection_message()
        assert "topic" in msg.lower()

    def test_workspace_name_from_topic(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        ctx.session_manager.get_topic_name.return_value = "my-project"
        rk = RoutingKey(user_id=1, chat_id=-100, session_key=42, thread_id=42)
        assert router.workspace_name(rk, ctx) == "my-project"

    def test_workspace_name_fallback(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        ctx.session_manager.get_topic_name.return_value = None
        rk = RoutingKey(user_id=1, chat_id=-100, session_key=42, thread_id=42)
        assert router.workspace_name(rk, ctx) == "topic-42"

    def test_get_window(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        ctx.session_manager.get_window_for_thread.return_value = "@5"
        rk = RoutingKey(user_id=1, chat_id=-100, session_key=42, thread_id=42)
        assert router.get_window(rk, ctx) == "@5"
        ctx.session_manager.get_window_for_thread.assert_called_once_with(1, 42)

    def test_bind_window(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        rk = RoutingKey(user_id=1, chat_id=-100, session_key=42, thread_id=42)
        router.bind_window(rk, "@5", "my-project", ctx)
        ctx.session_manager.bind_thread.assert_called_once_with(
            1, 42, "@5", window_name="my-project"
        )

    def test_unbind_window(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        ctx.session_manager.unbind_thread.return_value = "@5"
        rk = RoutingKey(user_id=1, chat_id=-100, session_key=42, thread_id=42)
        assert router.unbind_window(rk, ctx) == "@5"

    def test_store_chat_context(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        rk = RoutingKey(user_id=1, chat_id=-1001, session_key=42, thread_id=42)
        router.store_chat_context(rk, ctx)
        ctx.session_manager.set_group_chat_id.assert_called_once_with(1, 42, -1001)

    def test_send_kwargs(self):
        router = ForumRouter()
        rk = RoutingKey(user_id=1, chat_id=-100, session_key=42, thread_id=42)
        assert router.send_kwargs(rk) == {"message_thread_id": 42}

    def test_iter_bindings(self):
        router = ForumRouter()
        ctx = _make_agent_ctx()
        ctx.session_manager.iter_thread_bindings.return_value = [
            (1, 42, "@5"),
            (2, 99, "@7"),
        ]
        ctx.session_manager.resolve_chat_id.side_effect = lambda u, t: u
        bindings = router.iter_bindings(ctx)
        assert len(bindings) == 2
        rk0, wid0 = bindings[0]
        assert rk0.user_id == 1
        assert rk0.session_key == 42
        assert wid0 == "@5"

    @pytest.mark.asyncio
    async def test_probe_binding_exists_true(self):
        router = ForumRouter()
        bot = AsyncMock()
        ctx = _make_agent_ctx()
        ctx.session_manager.resolve_chat_id.return_value = -1001
        rk = RoutingKey(user_id=1, chat_id=-1001, session_key=42, thread_id=42)
        result = await router.probe_binding_exists(rk, bot, ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_probe_binding_exists_invalid_topic(self):
        from telegram.error import BadRequest

        router = ForumRouter()
        bot = AsyncMock()
        bot.unpin_all_forum_topic_messages.side_effect = BadRequest("Topic_id_invalid")
        ctx = _make_agent_ctx()
        ctx.session_manager.resolve_chat_id.return_value = -1001
        rk = RoutingKey(user_id=1, chat_id=-1001, session_key=42, thread_id=42)
        result = await router.probe_binding_exists(rk, bot, ctx)
        assert result is False


# --- GroupRouter tests ---


def _make_group_update(chat_id=-2001, user_id=456, chat_type="supergroup"):
    """Build a minimal Update for group mode testing."""
    update = MagicMock()
    update.message.message_thread_id = None
    update.message.chat.id = chat_id
    update.message.chat.type = chat_type
    update.message.chat.is_forum = False
    update.effective_user.id = user_id
    update.callback_query = None
    return update


class TestGroupRouter:
    def test_extract_routing_key_valid(self):
        router = GroupRouter()
        update = _make_group_update(chat_id=-2001, user_id=456)
        rk = router.extract_routing_key(update)
        assert rk is not None
        assert rk.user_id == 456
        assert rk.chat_id == -2001
        assert rk.session_key == -2001
        assert rk.thread_id is None

    def test_extract_routing_key_private_chat(self):
        router = GroupRouter()
        update = _make_group_update(chat_type="private")
        rk = router.extract_routing_key(update)
        assert rk is None

    def test_extract_routing_key_rejects_forum_supergroup(self):
        router = GroupRouter()
        update = _make_group_update(chat_type="supergroup")
        update.message.chat.is_forum = True
        rk = router.extract_routing_key(update)
        assert rk is None

    def test_rejection_message(self):
        router = GroupRouter()
        msg = router.rejection_message()
        assert "group" in msg.lower()

    def test_workspace_name_from_title(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        ctx.session_manager.get_group_title.return_value = "Dev Team"
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        assert router.workspace_name(rk, ctx) == "Dev Team"

    def test_workspace_name_fallback(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        ctx.session_manager.get_group_title.return_value = None
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        assert router.workspace_name(rk, ctx) == "group--2001"

    def test_get_window(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        ctx.session_manager.get_window_for_group.return_value = "@3"
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        assert router.get_window(rk, ctx) == "@3"
        ctx.session_manager.get_window_for_group.assert_called_once_with(-2001)

    def test_bind_window(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        router.bind_window(rk, "@3", "Dev Team", ctx)
        ctx.session_manager.bind_group.assert_called_once_with(-2001, "@3", "Dev Team")

    def test_unbind_window(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        ctx.session_manager.unbind_group.return_value = "@3"
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        assert router.unbind_window(rk, ctx) == "@3"

    def test_send_kwargs_empty(self):
        router = GroupRouter()
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        assert router.send_kwargs(rk) == {}

    def test_resolve_chat_id(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        assert router.resolve_chat_id(rk, ctx) == -2001

    def test_iter_bindings(self):
        router = GroupRouter()
        ctx = _make_agent_ctx("group")
        ctx.session_manager.iter_group_bindings.return_value = [
            (-2001, "@3"),
            (-2002, "@4"),
        ]
        bindings = router.iter_bindings(ctx)
        assert len(bindings) == 2
        rk0, wid0 = bindings[0]
        assert rk0.chat_id == -2001
        assert rk0.session_key == -2001
        assert rk0.user_id == 0  # group bindings are not per-user
        assert wid0 == "@3"

    @pytest.mark.asyncio
    async def test_probe_binding_exists_always_true(self):
        router = GroupRouter()
        bot = AsyncMock()
        ctx = _make_agent_ctx("group")
        rk = RoutingKey(user_id=1, chat_id=-2001, session_key=-2001, thread_id=None)
        result = await router.probe_binding_exists(rk, bot, ctx)
        assert result is True

    def test_register_lifecycle_handlers_noop(self):
        router = GroupRouter()
        app = MagicMock()
        router.register_lifecycle_handlers(app)
        app.add_handler.assert_not_called()
