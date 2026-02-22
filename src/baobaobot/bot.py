"""Telegram bot handlers — the main UI layer of BaoBaoClaude.

Registers all command/callback/message handlers and manages the bot lifecycle.
Each Telegram topic maps 1:1 to a tmux window (Claude session).

Core responsibilities:
  - Command handlers: /history, /screenshot, /esc, /forcekill,
    /agentsoul, /profile, /memory, /forget, /workspace, /rebuild,
    plus forwarding unknown /commands to Claude Code via tmux.
  - Callback query handler: history pagination,
    interactive UI navigation, screenshot refresh.
  - Topic-based routing: each named topic binds to one tmux window.
    Unbound topics auto-create a per-topic workspace and session.
  - Automatic cleanup: closing a topic kills the associated window
    (via router lifecycle handlers). Unsupported content (images, stickers, etc.)
    is rejected with a warning (unsupported_content_handler).
  - Bot lifecycle management: post_init, post_shutdown, create_bot.

Handler modules (in handlers/):
  - callback_data: Callback data constants
  - message_queue: Per-user message queue management
  - message_sender: Safe message sending helpers
  - history: Message history pagination
  - interactive_ui: Interactive UI handling
  - status_polling: Terminal status polling
  - response_builder: Response message building
  - persona_handler: /agentsoul command
  - profile_handler: /profile command
  - memory_handler: /memory, /forget commands

Key functions: create_bot(), handle_new_message().
"""

import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    Update,
    User,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from .agent_context import AgentContext
from .router import RoutingKey
from .handlers.callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
    CB_FILE_CANCEL,
    CB_FILE_DESC,
    CB_FILE_READ,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_KEYS_PREFIX,
    CB_RESTART_SESSION,
    CB_SCREENSHOT_REFRESH,
    CB_VERBOSITY,
    CB_VOICE_CANCEL,
    CB_VOICE_EDIT,
    CB_VOICE_SEND,
)
from .handlers.history import send_history
from .handlers.interactive_ui import (
    INTERACTIVE_TOOL_NAMES,
    clear_interactive_mode,
    clear_interactive_msg,
    get_interactive_msg_id,
    get_interactive_window,
    handle_interactive_ui,
    set_interactive_mode,
)
from .handlers.message_queue import (
    clear_status_msg_info,
    enqueue_content_message,
    get_message_queue,
    shutdown_workers,
)
from .handlers.message_sender import (
    NO_LINK_PREVIEW,
    rate_limit_send_message,
    safe_edit,
    safe_reply,
)
from .markdown_v2 import convert_markdown
from .handlers.response_builder import build_response_parts
from .handlers.status_polling import (
    clear_window_health,
    signal_shutdown,
    status_poll_loop,
)
from .screenshot import text_to_image
from .session_monitor import NewMessage, SessionMonitor, _SEND_FILE_RE
from .terminal_parser import extract_bash_output
from .handlers.persona_handler import (
    agentsoul_command,
    cancel_command,
    handle_edit_mode_message,
)
from .handlers.profile_handler import profile_command
from .handlers.memory_handler import forget_command, memory_command
from .handlers.verbosity_handler import (
    handle_verbosity_callback,
    should_skip_message,
    verbosity_command,
)
from .handlers.cron_handler import cron_command
from .persona.profile import (
    NAME_NOT_SET_SENTINELS,
    convert_user_mentions,
    ensure_user_profile,
)
from .workspace.assembler import ClaudeMdAssembler, rebuild_all_workspaces
from .workspace.manager import WorkspaceManager, refresh_all_skills

logger = logging.getLogger(__name__)

_MEMORY_TRIGGERS = ("記住", "remember", "記憶")


async def _send_typing(chat: object) -> None:
    """Send typing indicator (best-effort, never raises)."""
    try:
        await chat.send_action(ChatAction.TYPING)  # type: ignore[union-attr]
    except Exception:
        pass


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> AgentContext:
    """Retrieve the AgentContext stored in bot_data."""
    return context.bot_data["agent_ctx"]


def _agent_ctx(application: Application) -> AgentContext:
    """Retrieve the AgentContext from an Application instance."""
    return application.bot_data["agent_ctx"]


def _ensure_user_and_prefix(users_dir: Path, user: User, text: str) -> str:
    """Ensure user profile exists and return text with [Name|user_id] prefix.

    Args:
        users_dir: Path to users directory.
        user: Telegram User object.
        text: Original message text.

    Returns:
        Prefixed text like "[Alice|12345] original text".
    """
    first_name = user.first_name or ""
    username = user.username or ""

    profile = ensure_user_profile(users_dir, user.id, first_name, username)
    display = (
        profile.name
        if profile.name and profile.name not in NAME_NOT_SET_SENTINELS
        else first_name
    )
    return f"[{display}|{user.id}] {text}"


# Claude Code commands shown in bot menu (forwarded via tmux)
CC_COMMANDS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
    "cost": "↗ Show token/cost usage",
    "help": "↗ Show Claude Code help",
}


def _is_user_allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    return user_id is not None and _ctx(context).config.is_user_allowed(user_id)


def _resolve_rk(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> RoutingKey | None:
    """Extract a RoutingKey via the agent's router."""
    return _ctx(context).router.extract_routing_key(update)


def _get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update, returning None if not in a named topic.

    Still used by callback_handler for interactive UI where only thread_id is needed.
    """
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid


def _resolve_workspace_dir(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Path | None:
    """Resolve the per-topic workspace directory for the current routing key.

    Returns None if no bound window exists.
    """
    ctx = _ctx(context)
    rk = ctx.router.extract_routing_key(update)
    if rk is None:
        return None
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        return None
    display_name = ctx.session_manager.get_display_name(wid)
    return ctx.config.workspace_dir_for(display_name)


# --- Command handlers ---


async def forcekill_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kill the Claude process in the current session and restart it."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)
    if rk is None:
        await safe_reply(update.message, f"❌ {ctx.router.rejection_message()}")
        return
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        display = ctx.session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    display = ctx.session_manager.get_display_name(wid)
    await safe_reply(update.message, f"🔄 Restarting Claude in *{display}*…")

    success = await ctx.tmux_manager.restart_claude(wid)
    clear_window_health(wid)
    ctx.session_manager.clear_window_session(wid)

    if success:
        await safe_reply(update.message, f"✅ Claude restarted in *{display}*.")
    else:
        await safe_reply(update.message, f"❌ Failed to restart Claude in *{display}*.")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session or bound thread."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)
    if rk is None:
        await safe_reply(update.message, f"❌ {ctx.router.rejection_message()}")
        return
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    await send_history(update.message, wid, agent_ctx=ctx)


async def screenshot_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture the current tmux pane and send it as an image."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)
    if rk is None:
        await safe_reply(update.message, f"❌ {ctx.router.rejection_message()}")
        return
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        display = ctx.session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    text = await ctx.tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await safe_reply(update.message, "❌ Failed to capture pane content.")
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = _build_screenshot_keyboard(wid)
    await update.message.reply_document(
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=keyboard,
    )


async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Escape key to interrupt Claude."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)
    if rk is None:
        await safe_reply(update.message, f"❌ {ctx.router.rejection_message()}")
        return
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        display = ctx.session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    # Send Escape control character (no enter)
    await ctx.tmux_manager.send_keys(w.window_id, "\x1b", enter=False)
    await safe_reply(update.message, "⎋ Sent Escape")


# --- Workspace commands ---


async def workspace_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show workspace status for the current topic."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    workspace_dir = _resolve_workspace_dir(update, context)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ No workspace for this topic.")
        return

    await safe_reply(update.message, f"📁 **Workspace**\n\nPath: `{workspace_dir}`")


async def rebuild_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually rebuild CLAUDE.md for the current topic's workspace."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    workspace_dir = _resolve_workspace_dir(update, context)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ No workspace for this topic.")
        return

    ctx = _ctx(context)
    assembler = ClaudeMdAssembler(
        ctx.config.shared_dir, workspace_dir, locale=ctx.config.locale
    )
    assembler.write()
    await safe_reply(
        update.message,
        "✅ CLAUDE.md rebuilt. Send /clear to apply new settings to the current session.",
    )


# --- Screenshot keyboard with quick control keys ---

# key_id → (tmux_key, enter, literal)
_KEYS_SEND_MAP: dict[str, tuple[str, bool, bool]] = {
    "up": ("Up", False, False),
    "dn": ("Down", False, False),
    "lt": ("Left", False, False),
    "rt": ("Right", False, False),
    "esc": ("Escape", False, False),
    "ent": ("Enter", False, False),
    "spc": ("Space", False, False),
    "tab": ("Tab", False, False),
    "cc": ("C-c", False, False),
}

# key_id → display label (shown in callback answer toast)
_KEY_LABELS: dict[str, str] = {
    "up": "↑",
    "dn": "↓",
    "lt": "←",
    "rt": "→",
    "esc": "⎋ Esc",
    "ent": "⏎ Enter",
    "spc": "␣ Space",
    "tab": "⇥ Tab",
    "cc": "^C",
}


def _build_screenshot_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for screenshot: control keys + refresh."""

    def btn(label: str, key_id: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{CB_KEYS_PREFIX}{key_id}:{window_id}"[:64],
        )

    return InlineKeyboardMarkup(
        [
            [btn("␣ Space", "spc"), btn("↑", "up"), btn("⇥ Tab", "tab")],
            [btn("←", "lt"), btn("↓", "dn"), btn("→", "rt")],
            [btn("⎋ Esc", "esc"), btn("^C", "cc"), btn("⏎ Enter", "ent")],
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"{CB_SCREENSHOT_REFRESH}{window_id}"[:64],
                )
            ],
        ]
    )


async def forward_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Forward any non-bot command as a slash command to the active Claude Code session."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)
    if rk is not None:
        ctx.router.store_chat_context(rk, ctx)

    cmd_text = update.message.text or ""
    # The full text is already a slash command like "/clear" or "/compact foo"
    cc_slash = cmd_text.split("@")[0]  # strip bot mention
    wid = ctx.router.get_window(rk, ctx) if rk else None
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        display = ctx.session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    display = ctx.session_manager.get_display_name(wid)

    # Intercept /clear: trigger summary before clearing
    if cc_slash.strip().lower() == "/clear" and ctx.cron_service:
        logger.info("Triggering pre-clear summary for window %s", display)
        await safe_reply(update.message, f"📋 [{display}] Summarizing before clear...")
        try:
            summarized = await ctx.cron_service.trigger_summary(display)
            if summarized:
                await ctx.cron_service.wait_for_idle(wid)
        except Exception as e:
            logger.warning("Pre-clear summary failed: %s", e)

    logger.info(
        "Forwarding command %s to window %s (user=%d)", cc_slash, display, user.id
    )
    await _send_typing(update.message.chat)
    success, message = await ctx.session_manager.send_to_window(wid, cc_slash)
    if success:
        await safe_reply(update.message, f"⚡ [{display}] Sent: {cc_slash}")
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            logger.info("Clearing session for window %s after /clear", display)
            ctx.session_manager.clear_window_session(wid)
    else:
        await safe_reply(update.message, f"❌ {message}")


async def unsupported_content_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reply to truly unsupported content (stickers, etc.)."""
    if not update.message:
        return
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    logger.debug("Unsupported content from user %d", user.id)
    await safe_reply(
        update.message,
        "⚠ This message type is not supported (stickers, etc.).",
    )


def _resolve_tmp_dir(agent_ctx: AgentContext, window_id: str) -> Path | None:
    """Resolve the tmp/ directory for the workspace bound to a window.

    Returns None if cwd is unknown.
    """
    sm = agent_ctx.session_manager
    state = sm.get_window_state(window_id)
    if not state.cwd:
        return None
    tmp_dir = Path(state.cwd) / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    return tmp_dir


async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo/document/video/audio/voice — download to tmp/ and forward path to Claude."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)
    if rk is None:
        await safe_reply(
            update.message,
            f"❌ {ctx.router.rejection_message()}",
        )
        return

    ctx.router.store_chat_context(rk, ctx)
    thread_id = rk.thread_id

    # Check if topic is bound to a window
    wid = ctx.router.get_window(rk, ctx)
    if wid is None:
        await safe_reply(
            update.message,
            "❌ No session yet. Send a text message first to create a session, then send files.",
        )
        return

    # Resolve tmp directory
    tmp_dir = _resolve_tmp_dir(ctx, wid)
    if tmp_dir is None:
        await safe_reply(update.message, "❌ Cannot resolve workspace path.")
        return

    # Determine file object and filename
    msg = update.message
    file_obj = None
    original_name: str | None = None

    if msg.document:
        file_obj = await msg.document.get_file()
        original_name = msg.document.file_name
    elif msg.photo:
        # Use largest photo
        file_obj = await msg.photo[-1].get_file()
    elif msg.video:
        file_obj = await msg.video.get_file()
        original_name = msg.video.file_name
    elif msg.audio:
        file_obj = await msg.audio.get_file()
        original_name = msg.audio.file_name
    elif msg.voice:
        file_obj = await msg.voice.get_file()

    if file_obj is None:
        await safe_reply(update.message, "❌ Cannot retrieve file.")
        return

    # Generate filename (local time for human-readable timestamps)
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    if original_name:
        filename = f"{ts}_{original_name}"
    else:
        # Infer extension from file_path hint
        ext = ".jpg"
        if file_obj.file_path:
            fp = Path(file_obj.file_path)
            if fp.suffix:
                ext = fp.suffix
        if msg.voice:
            ext = ".ogg"
        elif msg.video and ext == ".jpg":
            ext = ".mp4"
        filename = f"file_{ts}{ext}"

    dest = tmp_dir / filename
    await file_obj.download_to_drive(str(dest))
    logger.info(
        "Downloaded file to %s (user=%d, session_key=%d)", dest, user.id, rk.session_key
    )

    users_dir = ctx.config.users_dir

    # Voice message: attempt transcription before sending to Claude
    if msg.voice:
        from .transcribe import transcribe_voice

        transcript = await transcribe_voice(
            dest, whisper_model=ctx.config.whisper_model
        )
        if transcript:
            caption = msg.caption or ""
            voice_key = (
                f"{user.id}_{rk.session_key}"
                f"_{int(datetime.now(tz=timezone.utc).timestamp())}"
            )
            pending_voice = context.bot_data.setdefault("_pending_voice", {})
            pending_voice[voice_key] = {
                "path": str(dest),
                "transcript": transcript,
                "caption": caption,
                "user_id": user.id,
                "session_key": rk.session_key,
                "thread_id": thread_id,
                "window_id": wid,
            }

            display = transcript
            if len(display) > 500:
                display = display[:500] + "..."

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Send",
                            callback_data=f"{CB_VOICE_SEND}{voice_key}",
                        ),
                        InlineKeyboardButton(
                            "✏️ Edit",
                            callback_data=f"{CB_VOICE_EDIT}{voice_key}",
                        ),
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"{CB_VOICE_CANCEL}{voice_key}",
                        ),
                    ]
                ]
            )
            await safe_reply(
                update.message,
                f"🎤 *Transcript:*\n{display}",
                reply_markup=keyboard,
            )
            return

    # Check caption for memory trigger words — delegate to Claude Code for analysis
    caption = msg.caption or ""
    if caption and any(t in caption.lower() for t in _MEMORY_TRIGGERS):
        # Remove only the first matched trigger word from description
        desc = caption
        for t in _MEMORY_TRIGGERS:
            idx = desc.lower().find(t)
            if idx >= 0:
                desc = (desc[:idx] + desc[idx + len(t) :]).strip()
                break
        # Forward to Claude Code with memory instruction
        lines = [f"[Memory Attachment] {dest}"]
        if desc:
            lines.append(f"User description: {desc}")
        raw_text = "\n".join(lines)
        text_to_send = _ensure_user_and_prefix(users_dir, user, raw_text)

        await _send_typing(msg.chat)
        success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
        if success:
            await safe_reply(
                update.message, f"💾 Sent for memory analysis: {dest.name}"
            )
        else:
            await safe_reply(
                update.message,
                f"❌ File saved but failed to send to Claude: {message}",
            )
        return

    # No caption — show inline keyboard asking user what to do
    if not caption:
        file_key = f"{user.id}_{rk.session_key}_{int(datetime.now(tz=timezone.utc).timestamp())}"
        pending = context.bot_data.setdefault("_pending_files", {})
        pending[file_key] = {
            "path": str(dest),
            "filename": filename,
            "user_id": user.id,
            "session_key": rk.session_key,
            "thread_id": thread_id,
            "window_id": wid,
        }
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📖 Read & Analyze", callback_data=f"{CB_FILE_READ}{file_key}"
                    ),
                    InlineKeyboardButton(
                        "✏️ Describe It", callback_data=f"{CB_FILE_DESC}{file_key}"
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel", callback_data=f"{CB_FILE_CANCEL}{file_key}"
                    ),
                ]
            ]
        )
        await safe_reply(
            update.message,
            f"📎 File received: *{filename}*\nWhat would you like to do?",
            reply_markup=keyboard,
        )
        return

    # Has caption — forward to Claude Code with user prefix
    lines = [f"[Received File] {dest}"]
    lines.append(caption)
    raw_text = "\n".join(lines)
    text_to_send = _ensure_user_and_prefix(users_dir, user, raw_text)

    await _send_typing(msg.chat)
    success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
    if success:
        await safe_reply(update.message, f"📎 File sent: {filename}")
    else:
        await safe_reply(
            update.message, f"❌ File saved but failed to send to Claude: {message}"
        )


def _cancel_bash_capture(bot_data: dict, user_id: int, session_key: int) -> None:
    """Cancel any running bash capture for this session key."""
    tasks: dict = bot_data.get("_bash_capture_tasks", {})
    key = (user_id, session_key)
    task = tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


async def _capture_bash_output(
    bot: Bot,
    agent_ctx: AgentContext,
    bot_data: dict,
    user_id: int,
    thread_id: int | None,
    window_id: str,
    command: str,
    *,
    task_key: tuple[int, int],
) -> None:
    """Background task: capture ``!`` bash command output from tmux pane.

    Sends the first captured output as a new message, then edits it
    in-place as more output appears.  Stops after 30 s or when cancelled
    (e.g. user sends a new message, which pushes content down).
    """
    try:
        # Wait for the command to start producing output
        await asyncio.sleep(2.0)

        chat_id = agent_ctx.session_manager.resolve_chat_id(user_id, thread_id)
        msg_id: int | None = None
        last_output: str = ""

        for _ in range(30):
            raw = await agent_ctx.tmux_manager.capture_pane(window_id)
            if raw is None:
                return

            output = extract_bash_output(raw, command)
            if not output:
                await asyncio.sleep(1.0)
                continue

            # Skip edit if nothing changed
            if output == last_output:
                await asyncio.sleep(1.0)
                continue

            last_output = output

            # Truncate to fit Telegram's 4096-char limit
            if len(output) > 3800:
                output = "… " + output[-3800:]

            if msg_id is None:
                # First capture — send a new message
                sent = await rate_limit_send_message(
                    bot,
                    chat_id,
                    output,
                    message_thread_id=thread_id,
                )
                if sent:
                    msg_id = sent.message_id
            else:
                # Subsequent captures — edit in place
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=convert_markdown(output),
                        parse_mode="MarkdownV2",
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                except Exception:
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=output,
                            link_preview_options=NO_LINK_PREVIEW,
                        )
                    except Exception:
                        pass

            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        return
    finally:
        tasks: dict = bot_data.get("_bash_capture_tasks", {})
        tasks.pop(task_key, None)


async def _auto_create_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rk: RoutingKey,
    text: str,
) -> None:
    """Auto-create a workspace and tmux window for an unbound routing key.

    Steps:
      1. Resolve workspace name via router
      2. Create workspace directory and init workspace files
      3. Assemble CLAUDE.md
      4. Create tmux window
      5. Bind via router → forward pending message
    """
    if not update.message:
        return

    ctx = _ctx(context)
    ws_name = ctx.router.workspace_name(rk, ctx)

    # Create per-topic workspace
    workspace_path = ctx.config.workspace_dir_for(ws_name)
    wm = WorkspaceManager(ctx.config.shared_dir, workspace_path)
    wm.init_workspace()

    # Assemble CLAUDE.md
    assembler = ClaudeMdAssembler(
        ctx.config.shared_dir, workspace_path, locale=ctx.config.locale
    )
    assembler.write()

    # Create tmux window
    success, message, created_wname, created_wid = await ctx.tmux_manager.create_window(
        str(workspace_path), window_name=ws_name
    )

    if not success:
        await safe_reply(update.message, f"❌ {message}")
        return

    logger.info(
        "Auto-created session: window=%s (id=%s) at %s (user=%d, key=%d)",
        created_wname,
        created_wid,
        workspace_path,
        rk.user_id,
        rk.session_key,
    )

    # Wait for Claude Code's SessionStart hook to register in session_map
    await ctx.session_manager.wait_for_session_map_entry(created_wid)

    # Bind via router
    ctx.router.bind_window(rk, created_wid, created_wname, ctx)
    ctx.router.store_chat_context(rk, ctx)

    # Forward the pending message with user prefix
    # user is guaranteed non-None here (caller already checked)
    user_obj = update.effective_user
    assert user_obj is not None
    prefixed_text = _ensure_user_and_prefix(ctx.config.users_dir, user_obj, text)
    send_ok, send_msg = await ctx.session_manager.send_to_window(
        created_wid, prefixed_text
    )
    if not send_ok:
        logger.warning("Failed to forward pending text: %s", send_msg)
        await safe_reply(
            update.message, f"✅ Session created, but message failed: {send_msg}"
        )
    else:
        await safe_reply(update.message, f"✅ Session created: {created_wname}")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    ctx = _ctx(context)
    rk = _resolve_rk(update, context)

    # Store chat context for message routing
    if rk is not None:
        ctx.router.store_chat_context(rk, ctx)

    # Backfill topic name from reply_to_message if not already persisted (forum mode)
    thread_id = rk.thread_id if rk else None
    if thread_id is not None and not ctx.session_manager.get_topic_name(thread_id):
        rtm = update.message.reply_to_message
        if rtm and rtm.forum_topic_created and rtm.forum_topic_created.name:
            ctx.session_manager.set_topic_name(thread_id, rtm.forum_topic_created.name)
            logger.debug(
                "Backfilled topic name from reply_to_message: thread=%d, name=%s",
                thread_id,
                rtm.forum_topic_created.name,
            )

    # Store group title for group mode
    if rk is not None and thread_id is None and update.message.chat.title:
        ctx.session_manager.set_group_title(rk.chat_id, update.message.chat.title)

    text = update.message.text

    # Check if user is in persona edit mode (e.g. /soul edit)
    if await handle_edit_mode_message(update, context):
        return

    # Check if user has a pending file waiting for description
    session_key = rk.session_key if rk else None
    if session_key is not None:
        pending = context.bot_data.get("_pending_files", {})
        for fk, info in list(pending.items()):
            if (
                info.get("user_id") == user.id
                and info.get("session_key") == session_key
                and info.get("waiting_description")
            ):
                pending.pop(fk)
                dest = info["path"]
                fname = info["filename"]
                wid = info["window_id"]
                raw_text = f"[Received File] {dest}\n{text}"
                text_to_send = _ensure_user_and_prefix(
                    ctx.config.users_dir, user, raw_text
                )
                await _send_typing(update.message.chat)
                success, message = await ctx.session_manager.send_to_window(
                    wid, text_to_send
                )
                if success:
                    await safe_reply(update.message, f"📎 Sent: {fname}")
                else:
                    await safe_reply(
                        update.message,
                        f"❌ Failed to send to Claude: {message}",
                    )
                return

    # Check if user has a pending voice transcript waiting for correction
    if session_key is not None:
        pending_voice = context.bot_data.get("_pending_voice", {})
        for vk, vinfo in list(pending_voice.items()):
            if (
                vinfo.get("user_id") == user.id
                and vinfo.get("session_key") == session_key
                and vinfo.get("waiting_correction")
            ):
                pending_voice.pop(vk)
                dest = vinfo["path"]
                wid = vinfo["window_id"]
                caption = vinfo.get("caption", "")
                lines = [f"[Voice Message] {dest}", f"Transcript: {text}"]
                if caption:
                    lines.append(caption)
                raw_text = "\n".join(lines)
                text_to_send = _ensure_user_and_prefix(
                    ctx.config.users_dir, user, raw_text
                )
                await _send_typing(update.message.chat)
                success, message = await ctx.session_manager.send_to_window(
                    wid, text_to_send
                )
                if success:
                    await safe_reply(update.message, "🎤 Sent corrected transcript")
                else:
                    await safe_reply(
                        update.message,
                        f"❌ Failed to send to Claude: {message}",
                    )
                return

    # Must have a valid routing key
    if rk is None:
        await safe_reply(
            update.message,
            f"❌ {ctx.router.rejection_message()}",
        )
        return

    wid = ctx.router.get_window(rk, ctx)
    if wid is None:
        # Unbound — auto-create workspace and session
        await _auto_create_session(update, context, rk, text)
        return

    # Bound — forward to bound window
    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        display = ctx.session_manager.get_display_name(wid)
        logger.info(
            "Stale binding: window %s gone, unbinding (user=%d, key=%d)",
            display,
            rk.user_id,
            rk.session_key,
        )
        ctx.router.unbind_window(rk, ctx)
        await safe_reply(
            update.message,
            f"❌ Window '{display}' no longer exists. Binding removed.\n"
            "Send a message to start a new session.",
        )
        return

    await _send_typing(update.message.chat)

    # Compute queue key: user_id for forum, chat_id for group
    queue_id = rk.user_id if rk.thread_id is not None else rk.chat_id
    clear_status_msg_info(ctx, queue_id, thread_id)

    # Cancel any running bash capture — new message pushes pane content down
    _cancel_bash_capture(context.bot_data, user.id, rk.session_key)

    # Add [Name|user_id] prefix for multi-user identification
    prefixed_text = _ensure_user_and_prefix(ctx.config.users_dir, user, text)
    success, message = await ctx.session_manager.send_to_window(wid, prefixed_text)
    if not success:
        await safe_reply(update.message, f"❌ {message}")
        return

    # Start background capture for ! bash command output
    if text.startswith("!") and len(text) > 1:
        bash_cmd = text[1:]  # strip leading "!"
        bash_tasks: dict = context.bot_data.setdefault("_bash_capture_tasks", {})
        tk = (user.id, rk.session_key)
        task = asyncio.create_task(
            _capture_bash_output(
                context.bot,
                ctx,
                context.bot_data,
                user.id,
                thread_id,
                wid,
                bash_cmd,
                task_key=tk,
            )
        )
        bash_tasks[tk] = task

    # If in interactive mode, refresh the UI after sending text
    interactive_window = get_interactive_window(ctx, queue_id, thread_id)
    if interactive_window and interactive_window == wid:
        await asyncio.sleep(0.2)
        await handle_interactive_ui(
            context.bot, queue_id, wid, thread_id, agent_ctx=ctx
        )


# --- Callback query handler ---


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        await query.answer("Not authorized")
        return

    ctx = _ctx(context)
    # Store group chat_id for forum topic message routing
    if query.message and query.message.chat.type in ("group", "supergroup"):
        cb_thread_id = _get_thread_id(update)
        if cb_thread_id is not None:
            ctx.session_manager.set_group_chat_id(
                user.id, cb_thread_id, query.message.chat.id
            )

    # Compute queue key for status/interactive state lookups.
    # Forum mode: user_id.  Group mode: chat_id.
    _cb_rk = _resolve_rk(update, context)
    queue_id = (
        _cb_rk.user_id
        if _cb_rk and _cb_rk.thread_id is not None
        else (query.message.chat.id if query.message else user.id)
    )

    data = query.data

    # History: older/newer pagination
    # Format: hp:<page>:<window_id>:<start>:<end> or hn:<page>:<window_id>:<start>:<end>
    if data.startswith(CB_HISTORY_PREV) or data.startswith(CB_HISTORY_NEXT):
        prefix_len = len(CB_HISTORY_PREV)  # same length for both
        rest = data[prefix_len:]
        try:
            parts = rest.split(":")
            if len(parts) < 4:
                # Old format without byte range: page:window_id
                offset_str, window_id = rest.split(":", 1)
                start_byte, end_byte = 0, 0
            else:
                # New format: page:window_id:start:end (window_id may contain colons)
                offset_str = parts[0]
                start_byte = int(parts[-2])
                end_byte = int(parts[-1])
                window_id = ":".join(parts[1:-2])
            offset = int(offset_str)
        except (ValueError, IndexError):
            await query.answer("Invalid data")
            return

        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await send_history(
                query,
                window_id,
                offset=offset,
                edit=True,
                start_byte=start_byte,
                end_byte=end_byte,
                agent_ctx=ctx,
            )
        else:
            await safe_edit(query, "Window no longer exists.")
        await query.answer("Page updated")

    # Screenshot: Refresh
    elif data.startswith(CB_SCREENSHOT_REFRESH):
        window_id = data[len(CB_SCREENSHOT_REFRESH) :]
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window no longer exists", show_alert=True)
            return

        text = await ctx.tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if not text:
            await query.answer("Failed to capture pane", show_alert=True)
            return

        png_bytes = await text_to_image(text, with_ansi=True)
        keyboard = _build_screenshot_keyboard(window_id)
        try:
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=io.BytesIO(png_bytes), filename="screenshot.png"
                ),
                reply_markup=keyboard,
            )
            await query.answer("Refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh screenshot: {e}")
            await query.answer("Failed to refresh", show_alert=True)

    # Restart session (freeze recovery)
    elif data.startswith(CB_RESTART_SESSION):
        window_id = data[len(CB_RESTART_SESSION) :]
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window no longer exists", show_alert=True)
            try:
                await query.edit_message_text("❌ Window no longer exists.")
            except Exception:
                pass
            return

        display = ctx.session_manager.get_display_name(window_id)
        success = await ctx.tmux_manager.restart_claude(window_id)
        clear_window_health(window_id)
        ctx.session_manager.clear_window_session(window_id)

        if success:
            try:
                await query.edit_message_text(f"✅ Claude restarted in {display}.")
            except Exception:
                pass
            await query.answer("Restarted")
        else:
            try:
                await query.edit_message_text(
                    f"❌ Failed to restart Claude in {display}."
                )
            except Exception:
                pass
            await query.answer("Restart failed", show_alert=True)

    # File action: Read & Analyze
    elif data.startswith(CB_FILE_READ):
        file_key = data[len(CB_FILE_READ) :]
        pending = context.bot_data.get("_pending_files", {})
        info = pending.pop(file_key, None)
        if not info:
            await query.answer("File no longer pending", show_alert=True)
            return
        wid = info["window_id"]
        dest = info["path"]
        fname = info["filename"]
        raw_text = (
            f"[Received File] {dest}\n"
            "Please read and analyze this file. "
            "Provide a brief summary of its content."
        )
        text_to_send = _ensure_user_and_prefix(ctx.config.users_dir, user, raw_text)
        success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
        if success:
            try:
                await query.edit_message_text(f"📖 Sent to AI for analysis: {fname}")
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(f"❌ Failed to send to Claude: {message}")
            except Exception:
                pass
        await query.answer()

    # File action: Describe It (wait for user text)
    elif data.startswith(CB_FILE_DESC):
        file_key = data[len(CB_FILE_DESC) :]
        pending = context.bot_data.get("_pending_files", {})
        info = pending.get(file_key)
        if not info:
            await query.answer("File no longer pending", show_alert=True)
            return
        info["waiting_description"] = True
        try:
            await query.edit_message_text(
                "✏️ Please describe what you'd like to do with this file:"
            )
        except Exception:
            pass
        await query.answer()

    # File action: Cancel
    elif data.startswith(CB_FILE_CANCEL):
        file_key = data[len(CB_FILE_CANCEL) :]
        pending = context.bot_data.get("_pending_files", {})
        pending.pop(file_key, None)
        try:
            await query.edit_message_text("❌ Cancelled.")
        except Exception:
            pass
        await query.answer()

    # Voice transcript: Send confirmed
    elif data.startswith(CB_VOICE_SEND):
        voice_key = data[len(CB_VOICE_SEND) :]
        pending_voice = context.bot_data.get("_pending_voice", {})
        info = pending_voice.pop(voice_key, None)
        if not info:
            await query.answer("Voice transcript expired", show_alert=True)
            return
        wid = info["window_id"]
        dest = info["path"]
        transcript = info["transcript"]
        caption = info.get("caption", "")
        lines = [f"[Voice Message] {dest}", f"Transcript: {transcript}"]
        if caption:
            lines.append(caption)
        raw_text = "\n".join(lines)
        text_to_send = _ensure_user_and_prefix(ctx.config.users_dir, user, raw_text)
        success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
        if success:
            short = transcript[:100] + ("..." if len(transcript) > 100 else "")
            try:
                await query.edit_message_text(f"🎤 Sent: {short}")
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(f"❌ Failed to send to Claude: {message}")
            except Exception:
                pass
        await query.answer()

    # Voice transcript: Edit (wait for corrected text)
    elif data.startswith(CB_VOICE_EDIT):
        voice_key = data[len(CB_VOICE_EDIT) :]
        pending_voice = context.bot_data.get("_pending_voice", {})
        info = pending_voice.get(voice_key)
        if not info:
            await query.answer("Voice transcript expired", show_alert=True)
            return
        info["waiting_correction"] = True
        transcript = info.get("transcript", "")
        try:
            await query.edit_message_text(transcript)
        except Exception:
            pass
        await query.answer()

    # Voice transcript: Cancel
    elif data.startswith(CB_VOICE_CANCEL):
        voice_key = data[len(CB_VOICE_CANCEL) :]
        pending_voice = context.bot_data.get("_pending_voice", {})
        pending_voice.pop(voice_key, None)
        try:
            await query.edit_message_text("❌ Voice message discarded.")
        except Exception:
            pass
        await query.answer()

    # Verbosity setting
    elif data.startswith(CB_VERBOSITY):
        await handle_verbosity_callback(query, ctx)

    elif data == "noop":
        await query.answer()

    # Interactive UI: Up arrow
    elif data.startswith(CB_ASK_UP):
        window_id = data[len(CB_ASK_UP) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Up", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer()

    # Interactive UI: Down arrow
    elif data.startswith(CB_ASK_DOWN):
        window_id = data[len(CB_ASK_DOWN) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Down", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer()

    # Interactive UI: Left arrow
    elif data.startswith(CB_ASK_LEFT):
        window_id = data[len(CB_ASK_LEFT) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Left", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer()

    # Interactive UI: Right arrow
    elif data.startswith(CB_ASK_RIGHT):
        window_id = data[len(CB_ASK_RIGHT) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Right", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer()

    # Interactive UI: Escape
    elif data.startswith(CB_ASK_ESC):
        window_id = data[len(CB_ASK_ESC) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Escape", enter=False, literal=False
            )
            await clear_interactive_msg(queue_id, context.bot, thread_id, agent_ctx=ctx)
        await query.answer("⎋ Esc")

    # Interactive UI: Enter
    elif data.startswith(CB_ASK_ENTER):
        window_id = data[len(CB_ASK_ENTER) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Enter", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer("⏎ Enter")

    # Interactive UI: Space
    elif data.startswith(CB_ASK_SPACE):
        window_id = data[len(CB_ASK_SPACE) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Space", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer("␣ Space")

    # Interactive UI: Tab
    elif data.startswith(CB_ASK_TAB):
        window_id = data[len(CB_ASK_TAB) :]
        thread_id = _get_thread_id(update)
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if w:
            await ctx.tmux_manager.send_keys(
                w.window_id, "Tab", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
            )
        await query.answer("⇥ Tab")

    # Interactive UI: refresh display
    elif data.startswith(CB_ASK_REFRESH):
        window_id = data[len(CB_ASK_REFRESH) :]
        thread_id = _get_thread_id(update)
        await handle_interactive_ui(
            context.bot, queue_id, window_id, thread_id, agent_ctx=ctx
        )
        await query.answer("🔄")

    # Screenshot quick keys: send key to tmux window
    elif data.startswith(CB_KEYS_PREFIX):
        rest = data[len(CB_KEYS_PREFIX) :]
        colon_idx = rest.find(":")
        if colon_idx < 0:
            await query.answer("Invalid data")
            return
        key_id = rest[:colon_idx]
        window_id = rest[colon_idx + 1 :]

        key_info = _KEYS_SEND_MAP.get(key_id)
        if not key_info:
            await query.answer("Unknown key")
            return

        tmux_key, enter, literal = key_info
        w = await ctx.tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window not found", show_alert=True)
            return

        await ctx.tmux_manager.send_keys(
            w.window_id, tmux_key, enter=enter, literal=literal
        )
        await query.answer(_KEY_LABELS.get(key_id, key_id))

        # Refresh screenshot after key press
        await asyncio.sleep(0.5)
        text = await ctx.tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if text:
            png_bytes = await text_to_image(text, with_ansi=True)
            keyboard = _build_screenshot_keyboard(window_id)
            try:
                await query.edit_message_media(
                    media=InputMediaDocument(
                        media=io.BytesIO(png_bytes),
                        filename="screenshot.png",
                    ),
                    reply_markup=keyboard,
                )
            except Exception:
                pass  # Screenshot unchanged or message too old


# --- Streaming response / notifications ---


async def handle_new_message(
    msg: NewMessage, bot: Bot, agent_ctx: AgentContext
) -> None:
    """Handle a new assistant message — enqueue for sequential processing.

    Messages are queued per-user to ensure status messages always appear last.
    Routes via thread_bindings (forum) or group_bindings (group) to deliver.
    """
    sm = agent_ctx.session_manager
    status = "complete" if msg.is_complete else "streaming"
    logger.info(
        f"handle_new_message [{status}]: session={msg.session_id}, "
        f"text_len={len(msg.text)}"
    )

    # Group mode: route to group chats
    if agent_ctx.config.mode == "group":
        active_groups = await sm.find_groups_for_session(msg.session_id)
        if not active_groups:
            logger.info(f"No active groups for session {msg.session_id}")
            return
        for chat_id, wid in active_groups:
            # In group mode, use chat_id as queue key
            # (group chat_ids are negative, so no collision with user_ids)
            await _deliver_message(msg, bot, agent_ctx, chat_id, wid, thread_id=None)
        return

    # Forum mode: route to users via thread_bindings
    active_users = await sm.find_users_for_session(msg.session_id)

    if not active_users:
        logger.info(f"No active users for session {msg.session_id}")
        return

    for user_id, wid, thread_id in active_users:
        await _deliver_message(msg, bot, agent_ctx, user_id, wid, thread_id=thread_id)


async def _send_files_background(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    files: list[Path],
) -> None:
    """Send files to Telegram in background (fire-and-forget).

    Runs as an asyncio task so it doesn't block the session monitor.
    Sends periodic heartbeat messages while uploading.
    """
    for fpath in files:
        try:
            suffix = fpath.suffix.lower()
            file_size = fpath.stat().st_size
            logger.info(
                "SEND_FILE: uploading %s (%d bytes) to chat_id=%s thread=%s",
                fpath.name,
                file_size,
                chat_id,
                thread_id,
            )
            # Launch upload with periodic heartbeat
            await _upload_with_heartbeat(
                bot, chat_id, thread_id, fpath, suffix, file_size
            )
            logger.info("Sent file to Telegram: %s", fpath)
        except Exception as e:
            logger.error("Failed to send file %s: %s", fpath, e)
            # Brief delay so httpx connection pool can recover after upload failure
            await asyncio.sleep(2)
            await _notify_send_error(bot, chat_id, thread_id, fpath.name, e)


_UPLOAD_TIMEOUT = 120
_HEARTBEAT_INTERVAL = 5


async def _upload_with_heartbeat(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    fpath: Path,
    suffix: str,
    file_size: int,
) -> None:
    """Upload a file with periodic heartbeat messages every 30s."""
    size_kb = file_size / 1024
    status_msg = await bot.send_message(
        chat_id=chat_id,
        text=f"📤 Uploading {fpath.name} ({size_kb:.0f} KB)...",
        message_thread_id=thread_id,
        connect_timeout=10,
        write_timeout=10,
        read_timeout=10,
    )
    msg_id = status_msg.message_id

    # Run upload and heartbeat as independent concurrent tasks.
    # Upload runs in a thread executor so httpx I/O cannot block the event loop.
    loop = asyncio.get_running_loop()
    upload_task = asyncio.create_task(
        _do_upload_in_executor(loop, bot, chat_id, thread_id, fpath, suffix)
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(bot, chat_id, msg_id, fpath.name, size_kb)
    )

    try:
        await asyncio.wait_for(upload_task, timeout=_UPLOAD_TIMEOUT)
        # Success: edit status to done
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"📤 Sent {fpath.name} ({size_kb:.0f} KB)",
            )
        except Exception:
            pass
    except Exception:
        # On failure, delete the status message (error notification sent by caller)
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=msg_id,
                connect_timeout=10,
                write_timeout=10,
                read_timeout=10,
            )
        except Exception:
            pass
        raise
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _heartbeat_loop(
    bot: Bot,
    chat_id: int,
    message_id: int,
    filename: str,
    size_kb: float,
) -> None:
    """Edit the status message every HEARTBEAT_INTERVAL seconds."""
    elapsed = 0
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        elapsed += _HEARTBEAT_INTERVAL
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"📤 Uploading {filename} ({size_kb:.0f} KB)... {elapsed}s elapsed",
            )
            logger.debug("SEND_FILE heartbeat: %s %ds elapsed", filename, elapsed)
        except Exception as e:
            logger.debug("SEND_FILE heartbeat edit failed: %s", e)


async def _do_upload_in_executor(
    loop: asyncio.AbstractEventLoop,
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    fpath: Path,
    suffix: str,
) -> None:
    """Upload a file in a thread executor so httpx I/O cannot block the event loop."""
    await loop.run_in_executor(
        None,
        lambda: _do_upload_sync(bot, chat_id, thread_id, fpath, suffix),
    )


def _do_upload_sync(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    fpath: Path,
    suffix: str,
) -> None:
    """Synchronous file upload — runs inside a thread executor."""
    import httpx

    url = f"https://api.telegram.org/bot{bot.token}/"
    timeout = httpx.Timeout(connect=30, write=30, read=60, pool=10)
    with httpx.Client(timeout=timeout) as client:
        with open(fpath, "rb") as f:
            files = {"document": (fpath.name, f)}
            data: dict[str, object] = {"chat_id": chat_id}
            if thread_id is not None:
                data["message_thread_id"] = thread_id

            if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                files = {"photo": (fpath.name, f)}
                resp = client.post(url + "sendPhoto", data=data, files=files)
            else:
                resp = client.post(url + "sendDocument", data=data, files=files)

        if resp.status_code != 200:
            body = resp.text[:200]
            raise RuntimeError(f"Telegram API {resp.status_code}: {body}")


async def _notify_send_error(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    filename: str,
    error: object,
) -> None:
    """Send file-send error notification to Telegram (best-effort, with retry)."""
    text = f"❌ Failed to send file {filename}: {error}"
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(2)
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                message_thread_id=thread_id,
                connect_timeout=10,
                write_timeout=10,
                read_timeout=10,
            )
            return
        except Exception as e2:
            logger.error(
                "Failed to notify file send error (attempt %d): %s", attempt + 1, e2
            )
    logger.error("All notification attempts failed for file %s", filename)


async def _deliver_message(
    msg: NewMessage,
    bot: Bot,
    agent_ctx: AgentContext,
    queue_id: int,
    wid: str,
    *,
    thread_id: int | None,
) -> None:
    """Deliver a NewMessage to a single destination.

    Args:
        msg: The message to deliver.
        bot: Telegram Bot instance.
        agent_ctx: Agent context.
        queue_id: Queue key (user_id in forum mode, chat_id in group mode).
        wid: Window ID.
        thread_id: message_thread_id for replies (None in group mode).
    """
    sm = agent_ctx.session_manager

    # Handle interactive tools specially - capture terminal and send UI
    if msg.tool_name in INTERACTIVE_TOOL_NAMES and msg.content_type == "tool_use":
        # Mark interactive mode BEFORE sleeping so polling skips this window
        set_interactive_mode(agent_ctx, queue_id, wid, thread_id or 0)
        # Flush pending messages (e.g. plan content) before sending interactive UI
        queue = get_message_queue(agent_ctx, queue_id)
        if queue:
            await queue.join()
        # Wait briefly for Claude Code to render the question UI
        await asyncio.sleep(0.3)
        handled = await handle_interactive_ui(
            bot, queue_id, wid, thread_id, agent_ctx=agent_ctx
        )
        if handled:
            # Update read offset
            session = await sm.resolve_session_for_window(wid)
            if session and session.file_path:
                try:
                    file_size = Path(session.file_path).stat().st_size
                    sm.update_user_window_offset(queue_id, wid, file_size)
                except OSError:
                    pass
            return  # Don't send the normal tool_use message
        else:
            # UI not rendered — clear the early-set mode
            clear_interactive_mode(agent_ctx, queue_id, thread_id or 0)

    # Any non-interactive message means the interaction is complete — delete the UI message
    if get_interactive_msg_id(agent_ctx, queue_id, thread_id or 0):
        await clear_interactive_msg(queue_id, bot, thread_id, agent_ctx=agent_ctx)

    # Verbosity filter — skip messages based on per-workspace setting
    # (interactive tools are already handled above and always shown)
    if msg.tool_name not in INTERACTIVE_TOOL_NAMES:
        verbosity = sm.get_verbosity(queue_id, thread_id or 0)
        if should_skip_message(msg.content_type, msg.role, verbosity):
            return

    # Send files if [SEND_FILE:path] markers are present
    if msg.file_paths:
        chat_id = (
            sm.resolve_chat_id(queue_id, thread_id)
            if thread_id is not None
            else queue_id
        )
        # Collect validated file paths, then send in background
        validated_files: list[Path] = []
        for fpath_str in msg.file_paths:
            fpath = Path(fpath_str)
            if not fpath.is_file():
                logger.warning("SEND_FILE: file not found: %s", fpath)
                asyncio.create_task(
                    _notify_send_error(
                        bot, chat_id, thread_id, fpath.name, "file not found"
                    )
                )
                continue
            # Security: file must be within agent directory
            try:
                fpath_resolved = fpath.resolve()
                config_resolved = agent_ctx.config.config_dir.resolve()
                if not str(fpath_resolved).startswith(str(config_resolved)):
                    logger.warning("SEND_FILE: path outside workspace: %s", fpath)
                    asyncio.create_task(
                        _notify_send_error(
                            bot,
                            chat_id,
                            thread_id,
                            fpath.name,
                            "path outside workspace",
                        )
                    )
                    continue
            except (OSError, ValueError):
                continue
            # Size check: Telegram limit ~50MB
            try:
                if fpath.stat().st_size > 50 * 1024 * 1024:
                    logger.warning("SEND_FILE: file too large: %s", fpath)
                    asyncio.create_task(
                        _notify_send_error(
                            bot,
                            chat_id,
                            thread_id,
                            fpath.name,
                            "file too large (>50MB)",
                        )
                    )
                    continue
            except OSError:
                continue
            validated_files.append(fpath)
        # Fire-and-forget: send files in background to avoid blocking monitor
        if validated_files:
            asyncio.create_task(
                _send_files_background(bot, chat_id, thread_id, validated_files)
            )
        # Strip [SEND_FILE:...] markers from the text
        msg = NewMessage(
            session_id=msg.session_id,
            text=_SEND_FILE_RE.sub("", msg.text).strip(),
            is_complete=msg.is_complete,
            content_type=msg.content_type,
            tool_use_id=msg.tool_use_id,
            role=msg.role,
            tool_name=msg.tool_name,
            file_paths=[],
        )
        if not msg.text:
            return

    # Convert @[user_id] markers to Telegram mentions
    display_text = convert_user_mentions(msg.text, agent_ctx.config.users_dir)

    parts = build_response_parts(
        display_text,
        msg.is_complete,
        msg.content_type,
        msg.role,
    )

    if msg.is_complete:
        # Enqueue content message task
        # Note: tool_result editing is handled inside _process_content_task
        # to ensure sequential processing with tool_use message sending
        await enqueue_content_message(
            bot=bot,
            user_id=queue_id,
            window_id=wid,
            parts=parts,
            tool_use_id=msg.tool_use_id,
            content_type=msg.content_type,
            text=msg.text,
            thread_id=thread_id,
            agent_ctx=agent_ctx,
        )

        # Update read offset to current file position
        session = await sm.resolve_session_for_window(wid)
        if session and session.file_path:
            try:
                file_size = Path(session.file_path).stat().st_size
                sm.update_user_window_offset(queue_id, wid, file_size)
            except OSError:
                pass


# --- App lifecycle ---


async def post_init(application: Application) -> None:
    agent_ctx = _agent_ctx(application)

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("history", "Message history for this topic"),
        BotCommand("screenshot", "Terminal screenshot with control keys"),
        BotCommand("esc", "Send Escape to interrupt Claude"),
        BotCommand("forcekill", "Kill & restart Claude in this session"),
        BotCommand("agentsoul", "View/edit agent personality & identity"),
        BotCommand("profile", "View/set user profile"),
        BotCommand("memory", "List/view/search memories"),
        BotCommand("forget", "Delete memory entries"),
        BotCommand("workspace", "Workspace status & project linking"),
        BotCommand("rebuild", "Rebuild CLAUDE.md"),
        BotCommand("cron", "Manage scheduled tasks"),
        BotCommand("verbosity", "Set message display verbosity"),
    ]
    # Add Claude Code slash commands
    for cmd_name, desc in CC_COMMANDS.items():
        bot_commands.append(BotCommand(cmd_name, desc))

    await application.bot.set_my_commands(bot_commands)

    # Re-resolve stale window IDs from persisted state against live tmux windows
    await agent_ctx.session_manager.resolve_stale_ids()

    # Rebuild CLAUDE.md and refresh skills for existing workspaces
    workspace_dirs = agent_ctx.config.iter_workspace_dirs()
    if workspace_dirs:
        rebuilt = rebuild_all_workspaces(
            agent_ctx.config.shared_dir,
            workspace_dirs,
            locale=agent_ctx.config.locale,
        )
        if rebuilt:
            logger.info(
                "Auto-rebuilt CLAUDE.md for %d workspace(s) on startup", rebuilt
            )
        refreshed = refresh_all_skills(agent_ctx.config.shared_dir, workspace_dirs)
        if refreshed:
            logger.info("Refreshed skills for %d workspace(s) on startup", refreshed)

    # Start cron service
    if agent_ctx.cron_service:
        await agent_ctx.cron_service.start()
        logger.info("Cron service started")

    monitor = SessionMonitor(
        tmux_manager=agent_ctx.tmux_manager,
        session_manager=agent_ctx.session_manager,
        session_map_file=agent_ctx.config.session_map_file,
        tmux_session_name=agent_ctx.config.tmux_session_name,
        projects_path=agent_ctx.config.claude_projects_path,
        poll_interval=agent_ctx.config.monitor_poll_interval,
        state_file=agent_ctx.config.monitor_state_file,
    )

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, application.bot, agent_ctx)

    monitor.set_message_callback(message_callback)
    monitor.start()
    agent_ctx.session_monitor = monitor
    logger.info("Session monitor started")

    # Start status polling task
    application.bot_data["_status_poll_task"] = asyncio.create_task(
        status_poll_loop(application.bot, agent_ctx=agent_ctx)
    )
    logger.info("Status polling task started")


async def post_shutdown(application: Application) -> None:
    agent_ctx = _agent_ctx(application)

    # Signal shutdown immediately to prevent destructive cleanup
    # (unbinding threads, killing windows) during the shutdown window.
    signal_shutdown()

    # Stop status polling
    poll_task = application.bot_data.get("_status_poll_task")
    if poll_task:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        application.bot_data["_status_poll_task"] = None
        logger.info("Status polling stopped")

    # Stop all queue workers
    await shutdown_workers(agent_ctx)

    # Stop cron service
    if agent_ctx.cron_service:
        await agent_ctx.cron_service.stop()
        logger.info("Cron service stopped")

    if agent_ctx.session_monitor:
        agent_ctx.session_monitor.stop()
        logger.info("Session monitor stopped")


def create_bot(agent_ctx: AgentContext) -> Application:
    request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=10.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    application = (
        Application.builder()
        .token(agent_ctx.config.bot_token)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Store agent context in bot_data for handler access
    application.bot_data["agent_ctx"] = agent_ctx

    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("forcekill", forcekill_command))
    application.add_handler(CommandHandler("screenshot", screenshot_command))
    application.add_handler(CommandHandler("esc", esc_command))
    # BaoBao persona/memory/workspace commands
    application.add_handler(CommandHandler("agentsoul", agentsoul_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("memory", memory_command))
    application.add_handler(CommandHandler("forget", forget_command))
    application.add_handler(CommandHandler("workspace", workspace_command))
    application.add_handler(CommandHandler("rebuild", rebuild_command))
    application.add_handler(CommandHandler("cron", cron_command))
    application.add_handler(CommandHandler("verbosity", verbosity_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Register mode-specific lifecycle handlers (e.g. topic created/closed for forum)
    agent_ctx.router.register_lifecycle_handlers(application)
    # Forward any other /command to Claude Code
    application.add_handler(MessageHandler(filters.COMMAND, forward_command_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    # File content: photos, documents, video, audio, voice → download and forward
    _file_filter = (
        filters.PHOTO
        | filters.Document.ALL
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
    )
    application.add_handler(MessageHandler(_file_filter, file_handler))
    # Catch-all: truly unsupported content (stickers, etc.)
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND
            & ~filters.TEXT
            & ~filters.StatusUpdate.ALL
            & ~_file_filter,
            unsupported_content_handler,
        )
    )

    return application
