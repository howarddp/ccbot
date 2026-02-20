"""Tests for topic name persistence in ForumRouter._topic_created_handler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from baobaobot.routers.forum import _topic_created_handler as topic_created_handler


def _make_update(thread_id, topic_name):
    """Build a minimal Update with forum_topic_created."""
    update = MagicMock()
    update.message.message_thread_id = thread_id
    ftc = MagicMock()
    ftc.name = topic_name
    update.message.forum_topic_created = ftc
    return update


def _make_context(mock_sm=None):
    """Build a minimal context with agent_ctx in bot_data."""
    ctx = AsyncMock()
    agent_ctx = MagicMock()
    agent_ctx.session_manager = mock_sm or MagicMock()
    ctx.bot_data = {"agent_ctx": agent_ctx}
    return ctx


class TestTopicCreatedHandler:
    @pytest.mark.asyncio
    async def test_persists_topic_name(self):
        """Verify name stored via session_manager.set_topic_name."""
        update = _make_update(thread_id=42, topic_name="my-project")
        mock_sm = MagicMock()
        context = _make_context(mock_sm)
        await topic_created_handler(update, context)
        mock_sm.set_topic_name.assert_called_once_with(42, "my-project")

    @pytest.mark.asyncio
    async def test_no_name(self):
        """No crash when topic name is None."""
        update = _make_update(thread_id=42, topic_name=None)
        mock_sm = MagicMock()
        context = _make_context(mock_sm)
        await topic_created_handler(update, context)
        mock_sm.set_topic_name.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_thread_id(self):
        """No crash when thread_id is None."""
        update = _make_update(thread_id=None, topic_name="some-name")
        mock_sm = MagicMock()
        context = _make_context(mock_sm)
        await topic_created_handler(update, context)
        mock_sm.set_topic_name.assert_not_called()
