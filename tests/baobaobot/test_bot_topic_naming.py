"""Tests for topic name persistence in bot.topic_created_handler."""

from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_context():
    """Build a minimal context."""
    ctx = AsyncMock()
    ctx.bot_data = {}
    return ctx


class TestTopicCreatedHandler:
    @pytest.mark.asyncio
    async def test_persists_topic_name(self):
        """Verify name stored via session_manager.set_topic_name."""
        update = _make_update(thread_id=42, topic_name="my-project")
        context = _make_context()
        with patch("baobaobot.bot.session_manager") as mock_sm:
            await topic_created_handler(update, context)
            mock_sm.set_topic_name.assert_called_once_with(42, "my-project")

    @pytest.mark.asyncio
    async def test_no_name(self):
        """No crash when topic name is None."""
        update = _make_update(thread_id=42, topic_name=None)
        context = _make_context()
        with patch("baobaobot.bot.session_manager") as mock_sm:
            await topic_created_handler(update, context)
            mock_sm.set_topic_name.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_thread_id(self):
        """No crash when thread_id is None."""
        update = _make_update(thread_id=None, topic_name="some-name")
        context = _make_context()
        with patch("baobaobot.bot.session_manager") as mock_sm:
            await topic_created_handler(update, context)
            mock_sm.set_topic_name.assert_not_called()
