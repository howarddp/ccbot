"""Tests for verbosity_handler.py — /verbosity command and callback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baobaobot.handlers.callback_data import CB_VERBOSITY
from baobaobot.handlers.verbosity_handler import (
    _build_verbosity_keyboard,
    _build_verbosity_text,
    handle_verbosity_callback,
    verbosity_command,
)


class TestBuildVerbosityKeyboard:
    def test_current_level_has_checkmark(self):
        kb = _build_verbosity_keyboard("normal", 100)
        buttons = kb.inline_keyboard[0]
        labels = [b.text for b in buttons]
        assert any("\u2705" in label and "normal" in label for label in labels)
        # Others should NOT have checkmark
        for b in buttons:
            if "normal" not in b.text:
                assert "\u2705" not in b.text

    def test_callback_data_format(self):
        kb = _build_verbosity_keyboard("quiet", 100)
        buttons = kb.inline_keyboard[0]
        for b in buttons:
            assert b.callback_data.startswith(CB_VERBOSITY)
            # Format: vb:<thread_id>:<level>
            payload = b.callback_data[len(CB_VERBOSITY) :]
            tid_str, level = payload.split(":", 1)
            assert tid_str == "100"
            assert level in ("quiet", "normal", "verbose")

    def test_three_levels(self):
        kb = _build_verbosity_keyboard("verbose", 0)
        buttons = kb.inline_keyboard[0]
        assert len(buttons) == 3


class TestBuildVerbosityText:
    def test_includes_current_level(self):
        text = _build_verbosity_text("quiet")
        assert "quiet" in text

    def test_includes_description(self):
        text = _build_verbosity_text("normal")
        assert "replies + tool summaries" in text


class TestVerbosityCommand:
    @pytest.mark.asyncio
    async def test_sends_keyboard(self):
        update = MagicMock()
        update.effective_user = MagicMock(id=42)
        update.message = MagicMock()
        update.message.message_thread_id = 100

        sm = MagicMock()
        sm.get_verbosity.return_value = "normal"
        agent_ctx = MagicMock()
        agent_ctx.session_manager = sm
        agent_ctx.config.is_user_allowed.return_value = True

        context = MagicMock()
        context.bot_data = {"agent_ctx": agent_ctx}

        with patch(
            "baobaobot.handlers.verbosity_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await verbosity_command(update, context)
            mock_reply.assert_called_once()
            args = mock_reply.call_args
            assert "normal" in args[0][1]
            assert args[1]["reply_markup"] is not None

        sm.get_verbosity.assert_called_once_with(42, 100)

    @pytest.mark.asyncio
    async def test_thread_id_none_defaults_to_zero(self):
        update = MagicMock()
        update.effective_user = MagicMock(id=42)
        update.message = MagicMock()
        update.message.message_thread_id = None

        sm = MagicMock()
        sm.get_verbosity.return_value = "normal"
        agent_ctx = MagicMock()
        agent_ctx.session_manager = sm
        agent_ctx.config.is_user_allowed.return_value = True

        context = MagicMock()
        context.bot_data = {"agent_ctx": agent_ctx}

        with patch(
            "baobaobot.handlers.verbosity_handler.safe_reply", new_callable=AsyncMock
        ):
            await verbosity_command(update, context)

        sm.get_verbosity.assert_called_once_with(42, 0)

    @pytest.mark.asyncio
    async def test_unauthorized_user_ignored(self):
        update = MagicMock()
        update.effective_user = MagicMock(id=42)
        update.message = MagicMock()

        agent_ctx = MagicMock()
        agent_ctx.config.is_user_allowed.return_value = False

        context = MagicMock()
        context.bot_data = {"agent_ctx": agent_ctx}

        with patch(
            "baobaobot.handlers.verbosity_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await verbosity_command(update, context)
            mock_reply.assert_not_called()


class TestHandleVerbosityCallback:
    @pytest.mark.asyncio
    async def test_sets_verbosity(self):
        query = MagicMock()
        query.from_user = MagicMock(id=42)
        query.data = f"{CB_VERBOSITY}100:quiet"
        query.answer = AsyncMock()

        sm = MagicMock()
        agent_ctx = MagicMock()
        agent_ctx.session_manager = sm

        with patch(
            "baobaobot.handlers.verbosity_handler.safe_edit", new_callable=AsyncMock
        ):
            await handle_verbosity_callback(query, agent_ctx)

        sm.set_verbosity.assert_called_once_with(42, 100, "quiet")
        query.answer.assert_called_once_with("Set to quiet")

    @pytest.mark.asyncio
    async def test_invalid_level_rejected(self):
        query = MagicMock()
        query.from_user = MagicMock(id=42)
        query.data = f"{CB_VERBOSITY}100:invalid"
        query.answer = AsyncMock()

        agent_ctx = MagicMock()

        await handle_verbosity_callback(query, agent_ctx)
        query.answer.assert_called_once_with("Invalid level")

    @pytest.mark.asyncio
    async def test_missing_colon_rejected(self):
        query = MagicMock()
        query.from_user = MagicMock(id=42)
        query.data = f"{CB_VERBOSITY}quiet"
        query.answer = AsyncMock()

        agent_ctx = MagicMock()

        await handle_verbosity_callback(query, agent_ctx)
        query.answer.assert_called_once_with("Invalid data")
