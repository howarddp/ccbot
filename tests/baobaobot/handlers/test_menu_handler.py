"""Tests for menu_handler.py — /agent, /system, /config menus and callbacks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baobaobot.handlers.callback_data import (
    CB_MENU_AGENT,
    CB_MENU_CONFIG,
    CB_MENU_SYSTEM,
)
from baobaobot.handlers.menu_handler import (
    _build_agent_keyboard,
    _build_config_keyboard,
    _build_system_keyboard,
    agent_command,
    config_command,
    handle_menu_callback,
    system_command,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _make_agent_ctx(*, allowed: bool = True, wid: str | None = "@1") -> MagicMock:
    """Build a minimal AgentContext mock."""
    ctx = MagicMock()
    ctx.config.is_user_allowed.return_value = allowed
    ctx.config.name = "baobaobot"
    ctx.config.shared_dir = "/tmp/shared"
    ctx.config.locale = "zh-TW"

    rk = MagicMock()
    ctx.router.extract_routing_key.return_value = rk
    ctx.router.get_window.return_value = wid

    sm = MagicMock()
    sm.get_display_name.return_value = "baobaobot/fun"
    state = MagicMock()
    state.session_id = "abc12345-dead-beef"
    state.cwd = "/Volumes/USB_DATA/.baobaobot/agents/baobaobot/workspace_fun"
    sm.get_window_state.return_value = state
    ctx.session_manager = sm
    ctx.cron_service = None

    tm = MagicMock()
    tm.find_window_by_id = AsyncMock(return_value=MagicMock(window_id=wid))
    tm.capture_pane = AsyncMock(return_value="❯ ")
    tm.send_keys = AsyncMock()
    tm.restart_claude = AsyncMock(return_value=True)
    ctx.tmux_manager = tm

    return ctx


def _make_context(agent_ctx: MagicMock) -> MagicMock:
    """Build a minimal telegram context mock."""
    context = MagicMock()
    context.bot_data = {"agent_ctx": agent_ctx}
    context.user_data = {}
    return context


def _make_update(*, user_id: int = 42, thread_id: int = 100) -> MagicMock:
    """Build a minimal Update mock with message."""
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.message = MagicMock()
    update.message.message_thread_id = thread_id
    return update


def _make_callback_update(
    data: str, *, user_id: int = 42, thread_id: int = 100
) -> tuple[MagicMock, MagicMock]:
    """Build Update + CallbackQuery mocks for callback testing."""
    query = MagicMock()
    query.data = data
    query.from_user = MagicMock(id=user_id)
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.message_thread_id = thread_id
    query.message.reply_text = AsyncMock()
    query.message.reply_document = AsyncMock()

    update = MagicMock()
    update.effective_user = MagicMock(id=user_id, first_name="Test", username="test")
    update.message = None  # callback updates have no .message
    update.callback_query = query
    return update, query


# ── Keyboard builder tests ─────────────────────────────────────────────


class TestBuildAgentKeyboard:
    def test_has_four_buttons(self):
        kb = _build_agent_keyboard("@1")
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        assert len(buttons) == 4

    def test_button_labels(self):
        kb = _build_agent_keyboard("@1")
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "⎋ Esc" in labels
        assert "🧹 Clear" in labels
        assert "📦 Compact" in labels
        assert "📊 Status" in labels

    def test_callback_data_prefix(self):
        kb = _build_agent_keyboard("@5")
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data.startswith(CB_MENU_AGENT)
                assert "@5" in btn.callback_data

    def test_callback_data_truncated_to_64(self):
        long_wid = "@" + "x" * 100
        kb = _build_agent_keyboard(long_wid)
        for row in kb.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data) <= 64


class TestBuildSystemKeyboard:
    def test_has_seven_buttons(self):
        kb = _build_system_keyboard("@1")
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        assert len(buttons) == 7

    def test_button_labels(self):
        kb = _build_system_keyboard("@1")
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        expected = [
            "📋 History",
            "📸 Screenshot",
            "🔄 Restart",
            "🔧 Rebuild",
            "⏰ Cron",
            "📊 Verbosity",
            "📂 Files",
        ]
        for label in expected:
            assert label in labels

    def test_callback_data_prefix(self):
        kb = _build_system_keyboard("@2")
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data.startswith(CB_MENU_SYSTEM)
                assert "@2" in btn.callback_data


class TestBuildConfigKeyboard:
    def test_has_two_buttons(self):
        kb = _build_config_keyboard()
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        assert len(buttons) == 2

    def test_button_labels(self):
        kb = _build_config_keyboard()
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "🫀 Agent Soul" in labels
        assert "👤 Profile" in labels

    def test_callback_data_prefix(self):
        kb = _build_config_keyboard()
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data.startswith(CB_MENU_CONFIG)

    def test_no_window_id_in_config(self):
        kb = _build_config_keyboard()
        for row in kb.inline_keyboard:
            for btn in row:
                assert "@" not in btn.callback_data


# ── Command handler tests ──────────────────────────────────────────────


class TestAgentCommand:
    @pytest.mark.asyncio
    async def test_sends_keyboard(self):
        agent_ctx = _make_agent_ctx()
        context = _make_context(agent_ctx)
        update = _make_update()

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await agent_command(update, context)
            mock_reply.assert_called_once()
            args = mock_reply.call_args
            assert "Agent" in args[0][1]
            assert args[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_no_session_shows_error(self):
        agent_ctx = _make_agent_ctx(wid=None)
        context = _make_context(agent_ctx)
        update = _make_update()

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await agent_command(update, context)
            mock_reply.assert_called_once()
            assert "No session" in mock_reply.call_args[0][1]

    @pytest.mark.asyncio
    async def test_unauthorized_user_ignored(self):
        agent_ctx = _make_agent_ctx(allowed=False)
        context = _make_context(agent_ctx)
        update = _make_update()

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await agent_command(update, context)
            mock_reply.assert_not_called()


class TestSystemCommand:
    @pytest.mark.asyncio
    async def test_sends_keyboard(self):
        agent_ctx = _make_agent_ctx()
        context = _make_context(agent_ctx)
        update = _make_update()

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await system_command(update, context)
            mock_reply.assert_called_once()
            args = mock_reply.call_args
            assert "System" in args[0][1]
            assert args[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_no_session_shows_error(self):
        agent_ctx = _make_agent_ctx(wid=None)
        context = _make_context(agent_ctx)
        update = _make_update()

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await system_command(update, context)
            assert "No session" in mock_reply.call_args[0][1]


class TestConfigCommand:
    @pytest.mark.asyncio
    async def test_sends_keyboard(self):
        agent_ctx = _make_agent_ctx()
        context = _make_context(agent_ctx)
        update = _make_update()

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await config_command(update, context)
            mock_reply.assert_called_once()
            args = mock_reply.call_args
            assert "Config" in args[0][1]
            assert args[1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_no_message_ignored(self):
        agent_ctx = _make_agent_ctx()
        context = _make_context(agent_ctx)
        update = MagicMock()
        update.effective_user = MagicMock(id=42)
        update.message = None

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await config_command(update, context)
            mock_reply.assert_not_called()


# ── Callback dispatch tests ────────────────────────────────────────────


class TestHandleMenuCallbackEsc:
    @pytest.mark.asyncio
    async def test_sends_escape_key(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_AGENT}esc:@1")

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ):
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

        agent_ctx.tmux_manager.send_keys.assert_called_once_with(
            "@1", "\x1b", enter=False
        )
        query.answer.assert_called_once_with("⎋ Sent Escape")


class TestHandleMenuCallbackClear:
    @pytest.mark.asyncio
    async def test_sends_clear_command(self):
        agent_ctx = _make_agent_ctx()
        agent_ctx.session_manager.send_to_window = AsyncMock(return_value=(True, ""))
        update, query = _make_callback_update(f"{CB_MENU_AGENT}clear:@1")

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ):
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

        agent_ctx.session_manager.send_to_window.assert_called_once_with("@1", "/clear")
        agent_ctx.session_manager.clear_window_session.assert_called_once_with("@1")


class TestHandleMenuCallbackCompact:
    @pytest.mark.asyncio
    async def test_sends_compact_command(self):
        agent_ctx = _make_agent_ctx()
        agent_ctx.session_manager.send_to_window = AsyncMock(return_value=(True, ""))
        update, query = _make_callback_update(f"{CB_MENU_AGENT}compact:@1")

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ):
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

        agent_ctx.session_manager.send_to_window.assert_called_once_with(
            "@1", "/compact"
        )


class TestHandleMenuCallbackStatus:
    @pytest.mark.asyncio
    async def test_shows_idle_status(self):
        agent_ctx = _make_agent_ctx()
        # capture_pane returns idle prompt
        agent_ctx.tmux_manager.capture_pane = AsyncMock(return_value="❯ ")
        update, query = _make_callback_update(f"{CB_MENU_AGENT}status:@1")

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

            mock_reply.assert_called_once()
            text = mock_reply.call_args[0][1]
            assert "Idle" in text
            assert "abc12345" in text
            assert "@1" in text

    @pytest.mark.asyncio
    async def test_shows_working_status(self):
        agent_ctx = _make_agent_ctx()
        # capture_pane returns active spinner
        agent_ctx.tmux_manager.capture_pane = AsyncMock(
            return_value="✻ Exploring codebase\n"
        )
        update, query = _make_callback_update(f"{CB_MENU_AGENT}status:@1")

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

            text = mock_reply.call_args[0][1]
            assert "Working" in text
            assert "Exploring codebase" in text


class TestHandleMenuCallbackHistory:
    @pytest.mark.asyncio
    async def test_calls_send_history(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_SYSTEM}history:@1")

        with patch(
            "baobaobot.handlers.menu_handler.send_history", new_callable=AsyncMock
        ) as mock_hist:
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

            mock_hist.assert_called_once()
            assert mock_hist.call_args[0][1] == "@1"


class TestHandleMenuCallbackRestart:
    @pytest.mark.asyncio
    async def test_restarts_claude(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_SYSTEM}restart:@1")

        with patch(
            "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
        ) as mock_reply:
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

            agent_ctx.tmux_manager.restart_claude.assert_called_once_with("@1")
            text = mock_reply.call_args[0][1]
            assert "restarted" in text


class TestHandleMenuCallbackWindowGone:
    @pytest.mark.asyncio
    async def test_window_gone_shows_alert(self):
        agent_ctx = _make_agent_ctx()
        agent_ctx.tmux_manager.find_window_by_id = AsyncMock(return_value=None)
        update, query = _make_callback_update(f"{CB_MENU_AGENT}esc:@99")

        await handle_menu_callback(
            update, _make_context(agent_ctx), query, query.data, agent_ctx
        )

        query.answer.assert_called_once_with("No session bound", show_alert=True)


class TestHandleMenuCallbackInvalidAction:
    @pytest.mark.asyncio
    async def test_unknown_agent_action(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_AGENT}unknown:@1")

        await handle_menu_callback(
            update, _make_context(agent_ctx), query, query.data, agent_ctx
        )

        query.answer.assert_called_once_with("Unknown action")

    @pytest.mark.asyncio
    async def test_unknown_config_action(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_CONFIG}unknown")

        await handle_menu_callback(
            update, _make_context(agent_ctx), query, query.data, agent_ctx
        )

        query.answer.assert_called_once_with("Unknown action")


class TestHandleMenuCallbackProfile:
    @pytest.mark.asyncio
    async def test_shows_profile(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_CONFIG}profile")

        mock_profile = MagicMock()
        mock_profile.name = "TestUser"
        mock_profile.telegram = "@testuser"
        mock_profile.timezone = "Asia/Taipei"
        mock_profile.language = "zh-TW"
        mock_profile.notes = None

        with (
            patch(
                "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
            patch(
                "baobaobot.persona.profile.read_user_profile",
                return_value=mock_profile,
            ),
            patch(
                "baobaobot.persona.profile.ensure_user_profile",
            ),
        ):
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

            mock_reply.assert_called_once()
            text = mock_reply.call_args[0][1]
            assert "TestUser" in text
            assert "Profile" in text


class TestHandleMenuCallbackAgentsoul:
    @pytest.mark.asyncio
    async def test_shows_agentsoul(self):
        agent_ctx = _make_agent_ctx()
        update, query = _make_callback_update(f"{CB_MENU_CONFIG}agentsoul")

        mock_identity = MagicMock()
        mock_identity.name = "BaoBao"
        mock_identity.emoji = "🐾"
        mock_identity.role = "AI Assistant"
        mock_identity.vibe = "warm"

        with (
            patch(
                "baobaobot.handlers.menu_handler.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
            patch(
                "baobaobot.persona.agentsoul.read_agentsoul_with_source",
                return_value=("# Agent Soul\nContent here", "shared"),
            ),
            patch(
                "baobaobot.persona.agentsoul.read_identity",
                return_value=mock_identity,
            ),
        ):
            await handle_menu_callback(
                update, _make_context(agent_ctx), query, query.data, agent_ctx
            )

            mock_reply.assert_called_once()
            text = mock_reply.call_args[0][1]
            assert "BaoBao" in text
            assert "🐾" in text
