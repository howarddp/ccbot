"""Telegram bot handlers — the main UI layer of BaoBaoClaude.

Registers all command/callback/message handlers and manages the bot lifecycle.
Each Telegram topic maps 1:1 to a tmux window (Claude session).

Core responsibilities:
  - Menu commands: /agent, /system, /config (inline keyboard menus)
  - Hidden alias commands: /history, /screenshot, /esc, /restart,
    /agentsoul, /profile, /memory, /forget, /workspace, /rebuild,
    plus forwarding unknown /commands to Claude Code via tmux.
  - Callback query handler: menu actions, history pagination,
    interactive UI navigation, screenshot refresh.
  - Topic-based routing: each named topic binds to one tmux window.
    Unbound topics auto-create a per-topic workspace and session.
  - Automatic cleanup: closing a topic kills the associated window
    (via router lifecycle handlers). Unsupported content (images, stickers, etc.)
    is rejected with a warning (unsupported_content_handler).
  - Bot lifecycle management: post_init, post_shutdown, create_bot.

Handler modules (in handlers/):
  - callback_data: Callback data constants
  - menu_handler: /agent, /system, /config menu commands
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
import os
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    Message,
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
    CB_LS_CLOSE,
    CB_LS_DIR,
    CB_LS_FILE,
    CB_LS_PAGE,
    CB_LS_UP,
    CB_MENU_AGENT,
    CB_MENU_CONFIG,
    CB_MENU_SYSTEM,
    CB_RESTART_SESSION,
    CB_SCREENSHOT_REFRESH,
    CB_VERBOSITY,
    CB_VOICE_CANCEL,
    CB_VOICE_EDIT,
    CB_VOICE_SEND,
)
from .handlers.file_browser import (
    LS_ENTRIES_KEY,
    LS_PATH_KEY,
    LS_ROOT_KEY,
    build_file_browser,
    clear_ls_state,
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
from .screenshot import cleanup_file_after, make_screenshot_url, text_to_image
from .session_monitor import (
    NewMessage,
    SessionMonitor,
    _SEND_FILE_RE,
    _SHARE_LINK_RE,
    _UPLOAD_LINK_RE,
)
from .terminal_parser import extract_bash_output
from .handlers.important_handler import handle_important_edit_message
from .handlers.workspace_resolver import resolve_workspace_for_window
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
from .handlers.menu_handler import (
    agent_command,
    config_command,
    handle_menu_callback,
    system_command,
)
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


_REPLY_PREVIEW_MAX = 100  # max chars of the replied-to message to include


def _extract_reply_context(message: Message) -> str:
    """Extract reply-to context from a Telegram message.

    Returns a '[Reply to: "..."]' line if the message is a reply,
    or an empty string otherwise.
    """
    rtm = message.reply_to_message
    if rtm is None:
        return ""
    # Skip forum-topic-created service messages (used for topic name backfill)
    if rtm.forum_topic_created:
        return ""
    original = rtm.text or rtm.caption or ""
    if not original:
        return ""
    preview = original[:_REPLY_PREVIEW_MAX]
    if len(original) > _REPLY_PREVIEW_MAX:
        preview += "..."
    return f'[Reply to: "{preview}"]\n'


# Claude Code commands forwarded via tmux (hidden aliases, not in bot menu)
CC_COMMANDS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
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
    """Resolve the per-topic workspace directory for the current routing key."""
    ctx = _ctx(context)
    rk = ctx.router.extract_routing_key(update)
    if rk is None:
        return None
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        return None
    return resolve_workspace_for_window(ctx, wid)


# --- Command handlers ---


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    png_bytes = await text_to_image(text, font_size=12, with_ansi=True)
    keyboard = _build_screenshot_keyboard(wid)
    url, tmp_path = await make_screenshot_url(
        png_bytes,
        agent_dir=Path(ctx.config.agent_dir),
        public_url=os.environ.get("SHARE_PUBLIC_URL", ""),
        share_server_running=ctx.share_server is not None,
    )
    if url and tmp_path:
        asyncio.create_task(cleanup_file_after(tmp_path))
        await update.message.reply_document(
            document=url,
            filename="screenshot.png",
            reply_markup=keyboard,
        )
    else:
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


async def ls_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Browse workspace files and directories via inline keyboard."""
    user = update.effective_user
    if not user or not _is_user_allowed(context, user.id):
        return
    if not update.message:
        return

    workspace_dir = _resolve_workspace_dir(update, context)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ No workspace for this topic.")
        return

    root = str(workspace_dir)
    # Support /ls subpath — jump directly to a subdirectory
    args = (update.message.text or "").split(None, 1)
    if len(args) > 1:
        target = (workspace_dir / args[1]).resolve()
        # Must stay within workspace
        try:
            target.relative_to(workspace_dir.resolve())
        except ValueError:
            target = workspace_dir
        if not target.is_dir():
            target = workspace_dir
        current = str(target)
    else:
        current = root

    text, keyboard, entries = build_file_browser(current, page=0, root_path=root)
    ud = context.user_data
    if ud is not None:
        ud[LS_PATH_KEY] = current
        ud[LS_ROOT_KEY] = root
        ud[LS_ENTRIES_KEY] = entries

    await safe_reply(update.message, text, reply_markup=keyboard)


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
        ctx.config.shared_dir,
        workspace_dir,
        locale=ctx.config.locale,
        allowed_users=ctx.config.allowed_users,
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


def _file_share_url(fpath: Path, agent_ctx: "AgentContext", ttl: int = 300) -> str | None:
    """Generate a share URL for a workspace file (bypasses ISP upload throttling).

    Checks if the file is within a registered workspace root and the share
    server is running. Returns a URL string or None.
    """
    public_url = os.environ.get("SHARE_PUBLIC_URL", "")
    if not public_url or not agent_ctx.share_server:
        return None
    try:
        from .share_server import generate_token

        agent_dir = Path(agent_ctx.config.agent_dir)
        ws_dirs = [Path(d) for d in (agent_ctx.config.iter_workspace_dirs() or [])]
        roots = ws_dirs + [agent_dir]
        fpath_resolved = fpath.resolve()
        for root in roots:
            root_resolved = root.resolve()
            try:
                rel = str(fpath_resolved.relative_to(root_resolved))
                token = generate_token(f"f:{root_resolved}:{rel}", ttl=ttl)
                encoded_rel = urllib.parse.quote(rel, safe="/")
                return f"{public_url}/f/{token}/{encoded_rel}"
            except ValueError:
                continue
        return None
    except Exception:
        return None


async def _send_file_via_url(
    bot: "Bot",
    chat_id: int,
    thread_id: int | None,
    fpath: Path,
    suffix: str,
    url: str,
    *,
    caption: str | None = None,
) -> None:
    """Send a file to Telegram via URL (Telegram fetches it — no ISP upload throttle)."""
    kw: dict[str, object] = {"chat_id": chat_id}
    if thread_id is not None:
        kw["message_thread_id"] = thread_id
    if caption:
        kw["caption"] = caption
    if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        await bot.send_photo(photo=url, **kw)
    else:
        await bot.send_document(document=url, filename=fpath.name, **kw)


def _ascii_safe_share_url(
    fpath: Path, agent_ctx: "AgentContext"
) -> tuple[str | None, Path | None]:
    """Get a share URL that Telegram can fetch.

    Telegram's download servers cannot handle percent-encoded non-ASCII characters
    in URL paths. For non-ASCII filenames, creates a temporary ASCII-named symlink
    and generates a URL pointing to that symlink (all-ASCII URL path).

    Returns (url, symlink_to_cleanup) where symlink_to_cleanup should be deleted
    after Telegram has fetched the file (or None if not needed).
    """
    if fpath.name.isascii():
        return _file_share_url(fpath, agent_ctx), None

    # Non-ASCII filename: create a temp symlink with an ASCII name
    suffix = fpath.suffix if fpath.suffix.isascii() else ""
    temp_name = f"_share_{uuid.uuid4().hex[:12]}{suffix}"
    temp_path = fpath.parent / temp_name
    try:
        os.symlink(fpath, temp_path)

        # Generate URL for the symlink path WITHOUT following the symlink.
        # _file_share_url() calls fpath.resolve() which follows symlinks back
        # to the Chinese filename, producing a percent-encoded URL that Telegram
        # cannot fetch. Instead, use os.path.abspath() which gives the symlink's
        # own absolute path (all ASCII).
        public_url = os.environ.get("SHARE_PUBLIC_URL", "")
        if not public_url or not agent_ctx.share_server:
            temp_path.unlink(missing_ok=True)
            return None, None

        from .share_server import generate_token

        temp_abs = Path(os.path.abspath(temp_path))
        agent_dir = Path(agent_ctx.config.agent_dir)
        ws_dirs = [Path(d) for d in (agent_ctx.config.iter_workspace_dirs() or [])]
        roots = ws_dirs + [agent_dir]
        url = None
        for root in roots:
            root_resolved = root.resolve()
            try:
                rel = str(temp_abs.relative_to(root_resolved))
                token = generate_token(f"f:{root_resolved}:{rel}", ttl=300)
                encoded_rel = urllib.parse.quote(rel, safe="/")
                url = f"{public_url}/f/{token}/{encoded_rel}"
                break
            except ValueError:
                continue

        if not url:
            logger.warning("Failed to find workspace root for symlink %s", temp_path)
            temp_path.unlink(missing_ok=True)
            return None, None
        logger.debug("ASCII symlink URL for %s → symlink: %s", fpath.name, temp_path.name)
        return url, temp_path
    except Exception as exc:
        logger.warning("Failed to create ASCII symlink for %s: %s", fpath.name, exc)
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None, None


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
        # Strip agent prefix for cron workspace lookup
        agent_prefix = f"{ctx.config.name}/"
        cron_ws_name = display.removeprefix(agent_prefix)
        try:
            summarized = await ctx.cron_service.trigger_summary(cron_ws_name)
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

    # ── Media group batching: check BEFORE any API calls ──
    # Voice messages are never part of a media group, so skip them.
    msg = update.message
    if not msg.voice and msg.media_group_id:
        _add_to_media_group(
            context.bot_data,
            msg.media_group_id,
            {"msg": msg, "tmp_dir": tmp_dir},
            caption=msg.caption or "",
            user_id=user.id,
            session_key=rk.session_key,
            thread_id=thread_id,
            wid=wid,
            ctx=ctx,
            users_dir=ctx.config.users_dir,
            user=user,
            bot=context.bot,
            chat_id=msg.chat_id,
        )
        return

    # Determine file object and filename
    file_obj = None
    original_name: str | None = None

    try:
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
    except Exception as exc:
        logger.warning("Failed to get file (user=%d): %s", user.id, exc)
        await safe_reply(
            update.message,
            "❌ File too large to download (Telegram Bot API limit: 20 MB).",
        )
        return

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

    if msg.voice:
        voice_dir = tmp_dir / "voice"
        voice_dir.mkdir(exist_ok=True)
        dest = voice_dir / filename
    else:
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


def _add_to_media_group(
    bot_data: dict,
    mg_id: str,
    file_info: dict,
    *,
    caption: str,
    user_id: int,
    session_key: int,
    thread_id: int | None,
    wid: str,
    ctx: "AgentContext",
    users_dir: Path,
    user: User,
    bot: Bot,
    chat_id: int,
) -> None:
    """Collect a file into the media-group buffer and (re)start the flush timer."""
    groups: dict = bot_data.setdefault("_media_groups", {})
    if mg_id in groups:
        buf = groups[mg_id]
        buf["files"].append(file_info)
        if caption and not buf["caption"]:
            buf["caption"] = caption
        # Cancel previous timer so we wait for more files
        timer = buf.get("timer_task")
        if timer and not timer.done():
            timer.cancel()
    else:
        buf = {
            "files": [file_info],
            "caption": caption,
            "user_id": user_id,
            "session_key": session_key,
            "thread_id": thread_id,
            "window_id": wid,
            "bot": bot,
            "chat_id": chat_id,
        }
        groups[mg_id] = buf

    # (Re)start 1.5s timer — only needs to cover Telegram update delivery gap (~0.5-1s)
    buf["timer_task"] = asyncio.create_task(
        _process_media_group_after_delay(
            bot_data, mg_id, ctx=ctx, users_dir=users_dir, user=user,
        )
    )


async def _process_media_group_after_delay(
    bot_data: dict,
    mg_id: str,
    *,
    ctx: "AgentContext",
    users_dir: Path,
    user: User,
) -> None:
    """Wait for the media group to settle, then download and handle the batch."""
    await asyncio.sleep(1.5)  # Only covers Telegram update delivery gap (~0.5-1s)
    groups: dict = bot_data.get("_media_groups", {})
    buf = groups.pop(mg_id, None)
    if not buf:
        return

    raw_files: list[dict] = buf["files"]  # Each has msg, tmp_dir
    caption: str = buf["caption"]
    wid: str = buf["window_id"]
    thread_id = buf["thread_id"]
    bot: Bot = buf["bot"]
    chat_id: int = buf["chat_id"]

    # --- get_file + download for all messages now (after batch is complete) ---
    files: list[dict] = []
    for rf in raw_files:
        msg: Message = rf["msg"]
        tmp_dir: Path = rf["tmp_dir"]
        try:
            file_obj = None
            original_name: str | None = None
            if msg.document:
                file_obj = await msg.document.get_file()
                original_name = msg.document.file_name
            elif msg.photo:
                file_obj = await msg.photo[-1].get_file()
            elif msg.video:
                file_obj = await msg.video.get_file()
                original_name = msg.video.file_name
            elif msg.audio:
                file_obj = await msg.audio.get_file()
                original_name = msg.audio.file_name
            if file_obj is None:
                continue
            # Generate filename
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            if original_name:
                filename = f"{ts}_{original_name}"
            else:
                ext = ".jpg"
                if file_obj.file_path:
                    fp = Path(file_obj.file_path)
                    if fp.suffix:
                        ext = fp.suffix
                if msg.video and ext == ".jpg":
                    ext = ".mp4"
                filename = f"file_{ts}{ext}"
            dest = tmp_dir / filename
            await file_obj.download_to_drive(str(dest))
            logger.info(
                "Media group download: %s (mg_id=%s)", dest, mg_id
            )
            files.append({"path": str(dest), "filename": filename})
        except Exception as exc:
            logger.warning(
                "Media group file failed (mg_id=%s): %s", mg_id, exc
            )
    if not files:
        try:
            await bot.send_message(
                chat_id,
                "❌ All file downloads failed.",
                message_thread_id=thread_id,
            )
        except Exception:
            pass
        return

    # --- caption contains memory trigger → batch memory attachment ---
    if caption and any(t in caption.lower() for t in _MEMORY_TRIGGERS):
        desc = caption
        for t in _MEMORY_TRIGGERS:
            idx = desc.lower().find(t)
            if idx >= 0:
                desc = (desc[:idx] + desc[idx + len(t) :]).strip()
                break
        lines = [f"[Memory Attachment] {f['path']}" for f in files]
        if desc:
            lines.append(f"User description: {desc}")
        raw_text = "\n".join(lines)
        text_to_send = _ensure_user_and_prefix(users_dir, user, raw_text)
        success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
        names = ", ".join(f["filename"] for f in files)
        try:
            if success:
                await bot.send_message(
                    chat_id, f"💾 Sent {len(files)} files for memory analysis",
                    message_thread_id=thread_id,
                )
            else:
                await bot.send_message(
                    chat_id,
                    f"❌ Files saved but failed to send to Claude: {message}",
                    message_thread_id=thread_id,
                )
        except Exception:
            pass
        return

    # --- has caption (non-memory) → send batch directly to Claude ---
    if caption:
        lines = [f"[Received File] {f['path']}" for f in files]
        lines.append(caption)
        raw_text = "\n".join(lines)
        text_to_send = _ensure_user_and_prefix(users_dir, user, raw_text)
        success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
        names = ", ".join(f["filename"] for f in files)
        try:
            if success:
                await bot.send_message(
                    chat_id, f"📎 Sent {len(files)} files: {names}",
                    message_thread_id=thread_id,
                )
            else:
                await bot.send_message(
                    chat_id,
                    f"❌ Failed to send to Claude: {message}",
                    message_thread_id=thread_id,
                )
        except Exception:
            pass
        return

    # --- no caption → show single inline keyboard for the group ---
    group_key = (
        f"{buf['user_id']}_{buf['session_key']}"
        f"_{int(datetime.now(tz=timezone.utc).timestamp())}"
    )
    pending: dict = bot_data.setdefault("_pending_files", {})
    pending[group_key] = {
        "paths": [f["path"] for f in files],
        "filenames": [f["filename"] for f in files],
        "path": files[0]["path"],
        "filename": files[0]["filename"],
        "user_id": buf["user_id"],
        "session_key": buf["session_key"],
        "thread_id": thread_id,
        "window_id": wid,
        "is_group": True,
    }
    names = ", ".join(f["filename"] for f in files)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📖 Read & Analyze",
                    callback_data=f"{CB_FILE_READ}{group_key}",
                ),
                InlineKeyboardButton(
                    "✏️ Describe It",
                    callback_data=f"{CB_FILE_DESC}{group_key}",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"{CB_FILE_CANCEL}{group_key}",
                ),
            ]
        ]
    )
    try:
        await bot.send_message(
            chat_id,
            f"📎 {len(files)} file{'s' if len(files) != 1 else ''} received: *{names}*\nWhat would you like to do?",
            message_thread_id=thread_id,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception:
        # Fallback without Markdown
        try:
            await bot.send_message(
                chat_id,
                f"📎 {len(files)} file{'s' if len(files) != 1 else ''} received: {names}\nWhat would you like to do?",
                message_thread_id=thread_id,
                reply_markup=keyboard,
            )
        except Exception:
            logger.warning("Failed to send media group keyboard for mg_id=%s", mg_id)


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
        ctx.config.shared_dir,
        workspace_path,
        locale=ctx.config.locale,
        allowed_users=ctx.config.allowed_users,
    )
    assembler.write()

    # Create tmux window with agent prefix: "agent_name/topic_name"
    tmux_window_name = f"{ctx.config.name}/{ws_name}"
    success, message, created_wname, created_wid = await ctx.tmux_manager.create_window(
        str(workspace_path), window_name=tmux_window_name
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

    # Bind via router — use topic-only name for display (not prefixed)
    ctx.router.bind_window(rk, created_wid, ws_name, ctx)
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

    # Check if user is in important instructions edit mode
    if await handle_important_edit_message(update, context):
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
                wid = info["window_id"]
                if info.get("is_group"):
                    lines = [f"[Received File] {p}" for p in info["paths"]]
                    lines.append(text)
                    raw_text = "\n".join(lines)
                    display = ", ".join(info["filenames"])
                else:
                    dest = info["path"]
                    display = info["filename"]
                    raw_text = f"[Received File] {dest}\n{text}"
                text_to_send = _ensure_user_and_prefix(
                    ctx.config.users_dir, user, raw_text
                )
                await _send_typing(update.message.chat)
                success, message = await ctx.session_manager.send_to_window(
                    wid, text_to_send
                )
                if success:
                    await safe_reply(update.message, f"📎 Sent: {display}")
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

    # Add reply context (if replying to a message) and user prefix
    reply_ctx = _extract_reply_context(update.message)
    prefixed_text = _ensure_user_and_prefix(
        ctx.config.users_dir, user, reply_ctx + text
    )
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

    # Menu commands (/agent, /system, /config)
    if (
        data.startswith(CB_MENU_AGENT)
        or data.startswith(CB_MENU_SYSTEM)
        or data.startswith(CB_MENU_CONFIG)
    ):
        await handle_menu_callback(update, context, query, data, ctx)
        return

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

        png_bytes = await text_to_image(text, font_size=12, with_ansi=True)
        keyboard = _build_screenshot_keyboard(window_id)
        url, tmp_path = await make_screenshot_url(
            png_bytes,
            agent_dir=Path(ctx.config.agent_dir),
            public_url=os.environ.get("SHARE_PUBLIC_URL", ""),
            share_server_running=ctx.share_server is not None,
        )
        if url and tmp_path:
            asyncio.create_task(cleanup_file_after(tmp_path))
        try:
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=url or io.BytesIO(png_bytes), filename="screenshot.png"
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
        if info.get("is_group"):
            paths = info["paths"]
            fnames = info["filenames"]
            lines = [f"[Received File] {p}" for p in paths]
            lines.append(
                "Please read and analyze these files. "
                "Provide a brief summary of their content."
            )
            display = ", ".join(fnames)
        else:
            dest = info["path"]
            fname = info["filename"]
            lines = [
                f"[Received File] {dest}",
                "Please read and analyze this file. "
                "Provide a brief summary of its content.",
            ]
            display = fname
        raw_text = "\n".join(lines)
        text_to_send = _ensure_user_and_prefix(ctx.config.users_dir, user, raw_text)
        success, message = await ctx.session_manager.send_to_window(wid, text_to_send)
        if success:
            try:
                await query.edit_message_text(f"📖 Sent to AI for analysis: {display}")
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(f"❌ Failed to send to Claude: {message}")
            except Exception:
                pass
        try:
            await query.answer()
        except Exception:
            pass

    # File action: Describe It (wait for user text)
    elif data.startswith(CB_FILE_DESC):
        file_key = data[len(CB_FILE_DESC) :]
        pending = context.bot_data.get("_pending_files", {})
        info = pending.get(file_key)
        if not info:
            try:
                await query.answer("File no longer pending", show_alert=True)
            except Exception:
                pass
            return
        info["waiting_description"] = True
        prompt = (
            "✏️ Please describe what you'd like to do with these files:"
            if info.get("is_group")
            else "✏️ Please describe what you'd like to do with this file:"
        )
        try:
            await query.edit_message_text(prompt)
        except Exception:
            pass
        try:
            await query.answer()
        except Exception:
            pass

    # File action: Cancel
    elif data.startswith(CB_FILE_CANCEL):
        file_key = data[len(CB_FILE_CANCEL) :]
        pending = context.bot_data.get("_pending_files", {})
        pending.pop(file_key, None)
        try:
            await query.edit_message_text("❌ Cancelled.")
        except Exception:
            pass
        try:
            await query.answer()
        except Exception:
            pass

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

    # File browser: enter directory
    elif data.startswith(CB_LS_DIR):
        idx = int(data[len(CB_LS_DIR) :])
        ud = context.user_data or {}
        entries = ud.get(LS_ENTRIES_KEY, [])
        root = ud.get(LS_ROOT_KEY)
        cur = ud.get(LS_PATH_KEY, root or "")
        if idx < len(entries):
            name, is_dir, _size = entries[idx]
            if is_dir:
                new_path = str(Path(cur) / name)
                text, keyboard, new_entries = build_file_browser(
                    new_path, page=0, root_path=root
                )
                if context.user_data is not None:
                    context.user_data[LS_PATH_KEY] = new_path
                    context.user_data[LS_ENTRIES_KEY] = new_entries
                await safe_edit(query, text, reply_markup=keyboard)
        await query.answer()

    # File browser: view/download file
    elif data.startswith(CB_LS_FILE):
        idx = int(data[len(CB_LS_FILE) :])
        ud = context.user_data or {}
        entries = ud.get(LS_ENTRIES_KEY, [])
        cur = ud.get(LS_PATH_KEY, "")
        if idx < len(entries):
            name, _is_dir, size = entries[idx]
            file_path = Path(cur) / name
            if file_path.is_file():
                max_inline = 50 * 1024  # 50 KB
                if size <= max_inline:
                    try:
                        content = file_path.read_text(errors="replace")[:4000]
                        await safe_edit(query, f"📄 `{name}`\n\n```\n{content}\n```")
                    except Exception:
                        await safe_edit(query, f"❌ Cannot read `{name}`.")
                else:
                    try:
                        await query.message.reply_document(
                            document=open(file_path, "rb"),  # noqa: SIM115
                            filename=name,
                        )
                        await query.answer(f"📤 {name}")
                    except Exception:
                        await safe_edit(query, f"❌ Cannot send `{name}`.")
            else:
                await query.answer("File not found", show_alert=True)
        else:
            await query.answer("Invalid index", show_alert=True)

    # File browser: go up
    elif data == CB_LS_UP:
        ud = context.user_data or {}
        root = ud.get(LS_ROOT_KEY)
        cur = ud.get(LS_PATH_KEY, root or "")
        parent = str(Path(cur).parent)
        # Don't go above root
        if root:
            try:
                Path(parent).resolve().relative_to(Path(root).resolve())
            except ValueError:
                parent = root
        text, keyboard, new_entries = build_file_browser(parent, page=0, root_path=root)
        if context.user_data is not None:
            context.user_data[LS_PATH_KEY] = parent
            context.user_data[LS_ENTRIES_KEY] = new_entries
        await safe_edit(query, text, reply_markup=keyboard)
        await query.answer()

    # File browser: pagination
    elif data.startswith(CB_LS_PAGE):
        page_num = int(data[len(CB_LS_PAGE) :])
        ud = context.user_data or {}
        root = ud.get(LS_ROOT_KEY)
        cur = ud.get(LS_PATH_KEY, root or "")
        text, keyboard, new_entries = build_file_browser(
            cur, page=page_num, root_path=root
        )
        if context.user_data is not None:
            context.user_data[LS_ENTRIES_KEY] = new_entries
        await safe_edit(query, text, reply_markup=keyboard)
        await query.answer()

    # File browser: close
    elif data == CB_LS_CLOSE:
        clear_ls_state(context.user_data)
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.edit_message_text("✕ Closed.")
            except Exception:
                pass
        await query.answer()

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
            png_bytes = await text_to_image(text, font_size=12, with_ansi=True)
            keyboard = _build_screenshot_keyboard(window_id)
            url, tmp_path = await make_screenshot_url(
                png_bytes,
                agent_dir=Path(ctx.config.agent_dir),
                public_url=os.environ.get("SHARE_PUBLIC_URL", ""),
                share_server_running=ctx.share_server is not None,
            )
            if url and tmp_path:
                asyncio.create_task(cleanup_file_after(tmp_path))
            try:
                await query.edit_message_media(
                    media=InputMediaDocument(
                        media=url or io.BytesIO(png_bytes),
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


_URL_SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip"}
_SHARE_LINK_SIZE_THRESHOLD = 20 * 1024 * 1024  # 20 MB


async def _send_files_background(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    files: list[Path],
    *,
    session_manager: "SessionManager | None" = None,
    wid: str = "",
    agent_ctx: "AgentContext | None" = None,
) -> None:
    """Send files to Telegram in background (fire-and-forget).

    Routing logic (to work around ISP upload throttling):
    - file >= 20 MB: send share-link (24h TTL) as text; skip upload entirely
    - suffix is image / PDF / ZIP and file < 20 MB: try Cloudflare URL; fallback to direct upload
    - other suffix (e.g. .txt) and file < 20 MB: direct upload (Telegram URL only supports PDF/ZIP)
    - no share server available: always direct upload

    If session_manager and wid are provided, notifies Claude on failure.
    """
    for fpath in files:
        try:
            suffix = fpath.suffix.lower()
            file_size = fpath.stat().st_size
            logger.info(
                "SEND_FILE: %s (%d bytes) to chat_id=%s thread=%s",
                fpath.name,
                file_size,
                chat_id,
                thread_id,
            )

            has_share_server = bool(agent_ctx and agent_ctx.share_server)

            # ── Large file (≥ 20 MB): send share-link, skip upload ──────────
            if file_size >= _SHARE_LINK_SIZE_THRESHOLD and has_share_server:
                share_url = _file_share_url(fpath, agent_ctx, ttl=86400)  # type: ignore[arg-type]
                if share_url:
                    size_mb = file_size / (1024 * 1024)
                    kw: dict[str, object] = {"chat_id": chat_id, "parse_mode": "HTML"}
                    if thread_id is not None:
                        kw["message_thread_id"] = thread_id
                    await bot.send_message(
                        text=(
                            f"📎 <b>{fpath.name}</b> ({size_mb:.1f} MB)\n"
                            f'<a href="{share_url}">Download link</a> (valid 24h)'
                        ),
                        **kw,
                    )
                    logger.info("SEND_FILE: sent share-link for large file %s", fpath.name)
                    continue
                # share_url is None (file outside workspace?): fall through to upload

            # ── URL-based send for image/PDF/ZIP (bypasses ISP throttle) ────
            if suffix in _URL_SUPPORTED_SUFFIXES and has_share_server:
                url, tmp_symlink = _ascii_safe_share_url(fpath, agent_ctx)  # type: ignore[arg-type]
                if url:
                    logger.info("SEND_FILE: using share URL for %s", fpath.name)
                    if tmp_symlink:
                        asyncio.create_task(cleanup_file_after(tmp_symlink, 300.0))
                    # When the URL uses an ASCII symlink, Telegram names the file
                    # after the symlink (e.g. _share_abc123.pdf). Add a caption to
                    # show the original non-ASCII filename to the user.
                    caption = fpath.name if tmp_symlink else None
                    try:
                        await _send_file_via_url(
                            bot, chat_id, thread_id, fpath, suffix, url, caption=caption
                        )
                        logger.info("Sent file to Telegram: %s", fpath)
                        continue
                    except Exception as url_err:
                        # Cloudflare quick tunnels are sometimes unreachable from
                        # Telegram's servers — fall back to direct upload.
                        logger.warning(
                            "SEND_FILE: URL-based send failed (%s), falling back to direct upload",
                            url_err,
                        )

            # ── Direct upload (other types, or URL failed) ───────────────────
            await _upload_with_heartbeat(
                bot, chat_id, thread_id, fpath, suffix, file_size
            )
            logger.info("Sent file to Telegram: %s", fpath)
        except Exception as e:
            logger.error("Failed to send file %s: %s", fpath, e)
            # Brief delay so httpx connection pool can recover after upload failure
            await asyncio.sleep(2)
            await _notify_send_error(bot, chat_id, thread_id, fpath.name, e)
            # Notify Claude so it can retry with share-link
            if session_manager and wid:
                hint = (
                    f"[System] Failed to send file {fpath.name} to Telegram: {e}. "
                    f"Use the share-link skill to send {fpath} as a download link instead."
                )
                await session_manager.send_to_window(wid, hint)


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
        connect_timeout=20,
        write_timeout=20,
        read_timeout=30,
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
                _send_files_background(
                    bot,
                    chat_id,
                    thread_id,
                    validated_files,
                    session_manager=sm,
                    wid=wid,
                    agent_ctx=agent_ctx,
                )
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
            share_links=msg.share_links,
            upload_links=msg.upload_links,
        )
        if not msg.text:
            return

    # Replace [SHARE_LINK:path] and [UPLOAD_LINK] markers with actual URLs
    if msg.share_links or msg.upload_links:
        public_url = os.environ.get("SHARE_PUBLIC_URL", "")
        text = msg.text
        if public_url:
            from .share_server import _resolve_relative, generate_token

            # Use the window's CWD as the workspace root for this session
            ws_cwd = agent_ctx.session_manager.get_window_state(wid).cwd
            ws_root = Path(ws_cwd) if ws_cwd else None
            # Fallback to agent config workspace dirs
            ws_roots = [Path(r) for r in (agent_ctx.config.iter_workspace_dirs() or [agent_ctx.config.agent_dir])]
            if ws_root and ws_root.is_dir():
                # Register this workspace with the share server dynamically
                if agent_ctx.share_server:
                    agent_ctx.share_server.add_workspace(ws_root)

            # Topic/group name for embedding in tokens
            display_name = agent_ctx.session_manager.get_display_name(wid) or ""

            # Replace [SHARE_LINK:path] with generated file/dir URL
            for path_str in msg.share_links:
                p = Path(path_str).resolve()
                # Determine which workspace this file belongs to
                result = _resolve_relative([ws_root] if ws_root else [], p)
                if result is None:
                    result = _resolve_relative(ws_roots, p)
                if result is None:
                    url = f"(file outside workspace: {path_str})"
                elif p.is_dir():
                    root, rel = result
                    if rel == ".":
                        rel = ""
                    token = generate_token(f"p:{root}:{rel}", name=display_name)
                    url = f"{public_url}/p/{token}/{rel}"
                elif p.is_file():
                    root, rel = result
                    token = generate_token(f"f:{root}:{rel}", name=display_name)
                    url = f"{public_url}/f/{token}/{rel}"
                else:
                    url = f"(file not found: {path_str})"
                text = text.replace(f"[SHARE_LINK:{path_str}]", url, 1)
            # Replace [UPLOAD_LINK] or [UPLOAD_LINK:ttl] with upload URL
            # Token encodes workspace so uploads go to the correct workspace
            upload_ws = ws_root if ws_root else (ws_roots[0] if ws_roots else None)
            for ttl_str in msg.upload_links:
                from .share_server import parse_ttl

                ttl = parse_ttl(ttl_str) if ttl_str else 1800
                token_path = f"upload:{upload_ws}" if upload_ws else "upload"
                token = generate_token(token_path, ttl=ttl, name=display_name)
                url = f"{public_url}/u/{token}"
                marker = f"[UPLOAD_LINK:{ttl_str}]" if ttl_str else "[UPLOAD_LINK]"
                text = text.replace(marker, url, 1)
        else:
            # Tunnel not running — strip markers and add notice
            text = _SHARE_LINK_RE.sub("(share server unavailable)", text)
            text = _UPLOAD_LINK_RE.sub("(share server unavailable)", text)
        msg = NewMessage(
            session_id=msg.session_id,
            text=text,
            is_complete=msg.is_complete,
            content_type=msg.content_type,
            tool_use_id=msg.tool_use_id,
            role=msg.role,
            tool_name=msg.tool_name,
            file_paths=msg.file_paths,
            share_links=[],
            upload_links=[],
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


def _write_runtime_env(agent_ctx: AgentContext, public_url: str) -> None:
    """Write dynamic runtime env vars to .runtime_env for bin scripts."""
    runtime_file = agent_ctx.config.config_dir / ".runtime_env"
    try:
        lines = [f"SHARE_PUBLIC_URL={public_url}"]
        # Also persist the secret so bin/share-link can use it
        share_secret = os.environ.get("SHARE_SECRET", "")
        if share_secret:
            lines.append(f"SHARE_SECRET={share_secret}")
        runtime_file.write_text("\n".join(lines) + "\n")
        logger.debug("Wrote runtime env to %s", runtime_file)
    except OSError:
        logger.warning("Failed to write runtime env file: %s", runtime_file)


async def _send_restart_complete(bot: Bot, agent_ctx: AgentContext) -> None:
    """Send 'Restart complete' to the agent's primary chat after startup."""
    sm = agent_ctx.session_manager
    cfg = agent_ctx.config
    targets: list[tuple[int, int | None]] = []  # (chat_id, thread_id | None)

    if cfg.mode == "group":
        # group_bindings: {chat_id (int) → window_id (str)}
        for chat_id in sm.group_bindings:
            targets.append((chat_id, None))
            break
    else:
        # Forum mode: first thread binding → resolve group_chat_id
        for uid, bindings in sm.thread_bindings.items():
            for tid in bindings:
                chat_id = sm.group_chat_ids.get(f"{uid}:{tid}")
                if chat_id:
                    targets.append((chat_id, tid))
                    break
            if targets:
                break

    for chat_id, thread_id in targets:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="✅ Restart complete.",
                message_thread_id=thread_id,
            )
        except Exception as e:
            logger.warning("Failed to send restart-complete notification: %s", e)


async def post_init(application: Application) -> None:
    agent_ctx = _agent_ctx(application)

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("agent", "Claude Code operations"),
        BotCommand("system", "System management"),
        BotCommand("config", "Personal settings"),
    ]

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
            allowed_users=agent_ctx.config.allowed_users,
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

    # Start share server + tunnel (only once across all agents — skip if already running)
    # Use a module-level flag (not env var, which may persist from parent process)
    # Clear stale env var from previous process on first init
    if not getattr(post_init, "_share_init_done", False):
        os.environ.pop("SHARE_PUBLIC_URL", None)
        post_init._share_init_done = True  # type: ignore[attr-defined]

    if getattr(post_init, "_share_started", False):
        # Register this agent's workspaces with the existing share server
        existing_server = getattr(post_init, "_share_server", None)
        if existing_server:
            for ws_dir in agent_ctx.config.iter_workspace_dirs():
                existing_server.add_workspace(ws_dir)
            agent_ctx.share_server = existing_server
        logger.info("Share server already started by another agent, registered workspaces")
    else:
        try:
            from .share_server import ShareServer
            from .tunnel import TunnelManager

            _SHARE_PORT = 8787

            # Collect ALL workspace roots from this agent's config
            workspace_roots = agent_ctx.config.iter_workspace_dirs()
            if not workspace_roots:
                workspace_roots = [agent_ctx.config.agent_dir]
            # Also add agent_dir itself as a root
            agent_dir = Path(agent_ctx.config.agent_dir)
            if agent_dir not in workspace_roots:
                workspace_roots.append(agent_dir)

            async def _on_upload(upload_dir: Path, filenames: list[str], description: str) -> None:
                """Notify the tmux window whose workspace received the upload."""
                # Build single-line notification — newlines in send_keys cause tmux
                # literal-mode issues (text truncation, Enter not delivered).
                files_part = ", ".join(f"{upload_dir / fn}" for fn in filenames)
                desc_part = f" (說明：{description})" if description else ""
                notify_text = (
                    f"[File Upload] 使用者上傳了 {len(filenames)} 個檔案：{files_part}{desc_part}"
                )
                logger.info(notify_text)
                # Determine which workspace the upload belongs to (upload_dir is {workspace}/tmp/uploads/...)
                upload_ws = str(upload_dir.resolve())
                try:
                    windows = await agent_ctx.tmux_manager.list_windows()
                    for w in windows:
                        # Use tmux pane CWD (works across all agents) instead of session_manager state
                        if w.cwd and upload_ws.startswith(w.cwd):
                            await agent_ctx.tmux_manager.send_keys(w.window_id, notify_text)
                            logger.info("Upload notification sent to window %s (%s)", w.window_id, w.cwd)
                except Exception:
                    logger.exception("Failed to notify tmux about upload")

            share_server = ShareServer(
                port=_SHARE_PORT,
                workspace_roots=workspace_roots,
                on_upload=_on_upload,
            )
            await share_server.start()
            agent_ctx.share_server = share_server

            def _on_url_change(new_url: str) -> None:
                os.environ["SHARE_PUBLIC_URL"] = new_url
                _write_runtime_env(agent_ctx, new_url)
                logger.info("Tunnel URL updated: %s", new_url)

            tunnel = TunnelManager(
                local_port=_SHARE_PORT,
                on_url_change=_on_url_change,
            )
            public_url = await tunnel.start()
            agent_ctx.tunnel_manager = tunnel

            # Set env var + write runtime file so bin/share-link can read it
            os.environ["SHARE_PUBLIC_URL"] = public_url
            _write_runtime_env(agent_ctx, public_url)
            post_init._share_started = True  # type: ignore[attr-defined]
            post_init._share_server = share_server  # type: ignore[attr-defined]
            logger.info("Share server + tunnel ready: %s", public_url)
        except Exception:
            logger.exception("Failed to start share server / tunnel (non-fatal)")

    monitor = SessionMonitor(
        tmux_manager=agent_ctx.tmux_manager,
        session_manager=agent_ctx.session_manager,
        session_map_file=agent_ctx.config.session_map_file,
        tmux_session_name=agent_ctx.config.tmux_session_name,
        projects_path=agent_ctx.config.claude_projects_path,
        poll_interval=agent_ctx.config.monitor_poll_interval,
        state_file=agent_ctx.config.monitor_state_file,
        agent_name=agent_ctx.config.name,
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

    # Send restart-complete notification if this is a restart
    if application.bot_data.get("_is_restart"):
        await _send_restart_complete(application.bot, agent_ctx)


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

    # Stop tunnel + share server
    if agent_ctx.tunnel_manager:
        await agent_ctx.tunnel_manager.stop()
        logger.info("Tunnel stopped")
    if agent_ctx.share_server:
        await agent_ctx.share_server.stop()
        logger.info("Share server stopped")
        # Clean up runtime env file
        runtime_file = agent_ctx.config.config_dir / ".runtime_env"
        runtime_file.unlink(missing_ok=True)


def create_bot(agent_ctx: AgentContext) -> Application:
    request = HTTPXRequest(
        connection_pool_size=32,
        connect_timeout=20.0,
        read_timeout=60.0,
        write_timeout=30.0,
        media_write_timeout=60.0,
        pool_timeout=15.0,
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

    # Menu commands (visible in bot menu)
    application.add_handler(CommandHandler("agent", agent_command))
    application.add_handler(CommandHandler("system", system_command))
    application.add_handler(CommandHandler("config", config_command))
    # Hidden aliases (individual commands still work when typed directly)
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("screenshot", screenshot_command))
    application.add_handler(CommandHandler("esc", esc_command))
    application.add_handler(CommandHandler("agentsoul", agentsoul_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("memory", memory_command))
    application.add_handler(CommandHandler("forget", forget_command))
    application.add_handler(CommandHandler("workspace", workspace_command))
    application.add_handler(CommandHandler("ls", ls_command))
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
