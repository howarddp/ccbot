"""Tests for topic name caching in bot.topic_created_handler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from baobaobot.bot import topic_created_handler


def _make_update(thread_id, topic_name):
    """Build a minimal Update with forum_topic_created."""
    update = MagicMock()
    update.message.message_thread_id = thread_id
    ftc = MagicMock()
    ftc.name = topic_name
    update.message.forum_topic_created = ftc
    return update


def _make_context(bot_data=None):
    """Build a minimal context with bot_data."""
    ctx = AsyncMock()
    ctx.bot_data = bot_data if bot_data is not None else {}
    return ctx


class TestTopicCreatedHandler:
    @pytest.mark.asyncio
    async def test_topic_created_handler_caches_name(self):
        """Verify name stored in bot_data['_topic_names']."""
        update = _make_update(thread_id=42, topic_name="my-project")
        context = _make_context()
        await topic_created_handler(update, context)
        assert context.bot_data["_topic_names"][42] == "my-project"

    @pytest.mark.asyncio
    async def test_topic_created_handler_no_name(self):
        """No crash when topic name is None."""
        update = _make_update(thread_id=42, topic_name=None)
        context = _make_context()
        await topic_created_handler(update, context)
        assert "_topic_names" not in context.bot_data or 42 not in context.bot_data.get(
            "_topic_names", {}
        )

    @pytest.mark.asyncio
    async def test_topic_created_handler_no_thread_id(self):
        """No crash when thread_id is None."""
        update = _make_update(thread_id=None, topic_name="some-name")
        context = _make_context()
        await topic_created_handler(update, context)
        assert "_topic_names" not in context.bot_data or None not in context.bot_data.get(
            "_topic_names", {}
        )
