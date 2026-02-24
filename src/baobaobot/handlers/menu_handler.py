"""Menu command handlers for /agent, /system, /config.

Provides inline keyboard menus that group related bot actions:
  - /agent: Claude Code operations (Esc, Clear, Compact, Status)
  - /system: System management (History, Screenshot, Restart, Rebuild, Cron, Verbosity, Files)
  - /config: Personal settings (Agent Soul, Profile)
"""

import io
import logging
from pathlib import Path

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..agent_context import AgentContext
from .callback_data import (
    CB_KEYS_PREFIX,
    CB_MENU_AGENT,
    CB_MENU_CONFIG,
    CB_MENU_SYSTEM,
    CB_SCREENSHOT_REFRESH,
)
from .history import send_history
from .message_sender import safe_reply
from .status_polling import clear_window_health

logger = logging.getLogger(__name__)


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> AgentContext:
    return context.bot_data["agent_ctx"]


def _resolve_wid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Resolve the window ID for the current routing key."""
    ctx = _ctx(context)
    rk = ctx.router.extract_routing_key(update)
    if rk is None:
        return None
    return ctx.router.get_window(rk, ctx)


# ── Keyboard builders ──────────────────────────────────────────────────


def _build_agent_keyboard(wid: str) -> InlineKeyboardMarkup:
    """Build /agent menu: 4 buttons in 2 rows."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "/esc",
                    callback_data=f"{CB_MENU_AGENT}esc:{wid}"[:64],
                ),
                InlineKeyboardButton(
                    "/clear",
                    callback_data=f"{CB_MENU_AGENT}clear:{wid}"[:64],
                ),
            ],
            [
                InlineKeyboardButton(
                    "/compact",
                    callback_data=f"{CB_MENU_AGENT}compact:{wid}"[:64],
                ),
                InlineKeyboardButton(
                    "/status",
                    callback_data=f"{CB_MENU_AGENT}status:{wid}"[:64],
                ),
            ],
        ]
    )


def _build_system_keyboard(wid: str) -> InlineKeyboardMarkup:
    """Build /system menu: 7 buttons in 4 rows."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📋 History",
                    callback_data=f"{CB_MENU_SYSTEM}history:{wid}"[:64],
                ),
                InlineKeyboardButton(
                    "📸 Screenshot",
                    callback_data=f"{CB_MENU_SYSTEM}screenshot:{wid}"[:64],
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Restart",
                    callback_data=f"{CB_MENU_SYSTEM}restart:{wid}"[:64],
                ),
                InlineKeyboardButton(
                    "🔧 Rebuild",
                    callback_data=f"{CB_MENU_SYSTEM}rebuild:{wid}"[:64],
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏰ Cron",
                    callback_data=f"{CB_MENU_SYSTEM}cron:{wid}"[:64],
                ),
                InlineKeyboardButton(
                    "📊 Verbosity",
                    callback_data=f"{CB_MENU_SYSTEM}verbosity:{wid}"[:64],
                ),
            ],
            [
                InlineKeyboardButton(
                    "📂 Files",
                    callback_data=f"{CB_MENU_SYSTEM}ls:{wid}"[:64],
                ),
            ],
        ]
    )


def _build_config_keyboard() -> InlineKeyboardMarkup:
    """Build /config menu: 2 buttons in 1 row."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🫀 Agent Soul",
                    callback_data=f"{CB_MENU_CONFIG}agentsoul",
                ),
                InlineKeyboardButton(
                    "👤 Profile",
                    callback_data=f"{CB_MENU_CONFIG}profile",
                ),
            ],
        ]
    )


# ── Command handlers ───────────────────────────────────────────────────


async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show /agent inline keyboard menu."""
    user = update.effective_user
    if not user or not update.message:
        return
    ctx = _ctx(context)
    if not ctx.config.is_user_allowed(user.id):
        return

    wid = _resolve_wid(update, context)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    keyboard = _build_agent_keyboard(wid)
    await safe_reply(update.message, "⚡ *Agent*", reply_markup=keyboard)


async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show /system inline keyboard menu."""
    user = update.effective_user
    if not user or not update.message:
        return
    ctx = _ctx(context)
    if not ctx.config.is_user_allowed(user.id):
        return

    wid = _resolve_wid(update, context)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    keyboard = _build_system_keyboard(wid)
    await safe_reply(update.message, "🔧 *System*", reply_markup=keyboard)


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show /config inline keyboard menu."""
    user = update.effective_user
    if not user or not update.message:
        return
    ctx = _ctx(context)
    if not ctx.config.is_user_allowed(user.id):
        return

    keyboard = _build_config_keyboard()
    await safe_reply(update.message, "⚙️ *Config*", reply_markup=keyboard)


# ── Callback dispatcher ───────────────────────────────────────────────


async def handle_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: CallbackQuery,
    data: str,
    ctx: AgentContext,
) -> None:
    """Dispatch menu callback queries (mn:a:*, mn:s:*, mn:c:*)."""
    if data.startswith(CB_MENU_AGENT):
        rest = data[len(CB_MENU_AGENT) :]
        await _dispatch_agent(query, rest, ctx)
    elif data.startswith(CB_MENU_SYSTEM):
        rest = data[len(CB_MENU_SYSTEM) :]
        await _dispatch_system(update, context, query, rest, ctx)
    elif data.startswith(CB_MENU_CONFIG):
        rest = data[len(CB_MENU_CONFIG) :]
        await _dispatch_config(update, context, query, rest, ctx)


async def _dispatch_agent(
    query: CallbackQuery,
    rest: str,
    ctx: AgentContext,
) -> None:
    """Handle mn:a:<action>:<window_id> callbacks."""
    parts = rest.split(":", 1)
    if len(parts) < 2:
        await query.answer("Invalid data")
        return
    action, wid = parts[0], parts[1]

    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        await query.answer("No session bound", show_alert=True)
        return

    if action == "esc":
        await _handle_esc(query, ctx, wid)
    elif action == "clear":
        await _handle_clear(query, ctx, wid)
    elif action == "compact":
        await _handle_compact(query, ctx, wid)
    elif action == "status":
        await _handle_status(query, ctx, wid)
    else:
        await query.answer("Unknown action")


async def _dispatch_system(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: CallbackQuery,
    rest: str,
    ctx: AgentContext,
) -> None:
    """Handle mn:s:<action>:<window_id> callbacks."""
    parts = rest.split(":", 1)
    if len(parts) < 2:
        await query.answer("Invalid data")
        return
    action, wid = parts[0], parts[1]

    w = await ctx.tmux_manager.find_window_by_id(wid)
    if not w:
        await query.answer("No session bound", show_alert=True)
        return

    if action == "history":
        await _handle_history(query, ctx, wid)
    elif action == "screenshot":
        await _handle_screenshot(query, ctx, wid)
    elif action == "restart":
        await _handle_restart(query, ctx, wid)
    elif action == "rebuild":
        await _handle_rebuild(query, ctx, wid)
    elif action == "cron":
        await _handle_cron(query, update, context)
    elif action == "verbosity":
        await _handle_verbosity(query, update, context)
    elif action == "ls":
        await _handle_ls(query, update, context)
    else:
        await query.answer("Unknown action")


async def _dispatch_config(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: CallbackQuery,
    rest: str,
    ctx: AgentContext,
) -> None:
    """Handle mn:c:<action> callbacks."""
    action = rest

    if action == "agentsoul":
        await _handle_agentsoul(query, update, context)
    elif action == "profile":
        await _handle_profile(query, update, context)
    else:
        await query.answer("Unknown action")


# ── Action handlers ────────────────────────────────────────────────────


async def _handle_esc(query: CallbackQuery, ctx: AgentContext, wid: str) -> None:
    """Send Escape key to tmux."""
    await ctx.tmux_manager.send_keys(wid, "\x1b", enter=False)
    await query.answer("⎋ Sent Escape")
    if query.message:
        await safe_reply(query.message, "⎋ Sent Escape")


async def _handle_clear(
    query: CallbackQuery,
    ctx: AgentContext,
    wid: str,
) -> None:
    """Trigger pre-clear summary + forward /clear to tmux."""
    display = ctx.session_manager.get_display_name(wid)
    await query.answer("🧹 Clearing...")

    # Pre-clear summary
    if ctx.cron_service:
        logger.info("Triggering pre-clear summary for window %s", display)
        if query.message:
            await safe_reply(
                query.message, f"📋 [{display}] Summarizing before clear..."
            )
        agent_prefix = f"{ctx.config.name}/"
        cron_ws_name = display.removeprefix(agent_prefix)
        try:
            summarized = await ctx.cron_service.trigger_summary(cron_ws_name)
            if summarized:
                await ctx.cron_service.wait_for_idle(wid)
        except Exception as e:
            logger.warning("Pre-clear summary failed: %s", e)

    success, message = await ctx.session_manager.send_to_window(wid, "/clear")
    if success:
        ctx.session_manager.clear_window_session(wid)
        if query.message:
            await safe_reply(query.message, f"🧹 [{display}] Sent: /clear")
    elif query.message:
        await safe_reply(query.message, f"❌ {message}")


async def _handle_compact(query: CallbackQuery, ctx: AgentContext, wid: str) -> None:
    """Forward /compact to tmux."""
    display = ctx.session_manager.get_display_name(wid)
    success, message = await ctx.session_manager.send_to_window(wid, "/compact")
    if success:
        await query.answer("📦 Compacting...")
        if query.message:
            await safe_reply(query.message, f"📦 [{display}] Sent: /compact")
    else:
        await query.answer(f"❌ {message}", show_alert=True)


async def _handle_status(query: CallbackQuery, ctx: AgentContext, wid: str) -> None:
    """Forward /status to Claude Code via tmux."""
    display = ctx.session_manager.get_display_name(wid)
    success, message = await ctx.session_manager.send_to_window(wid, "/status")
    if success:
        await query.answer("📊 Checking status...")
        if query.message:
            await safe_reply(query.message, f"📊 [{display}] Sent: /status")
    else:
        await query.answer(f"❌ {message}", show_alert=True)


async def _handle_history(query: CallbackQuery, ctx: AgentContext, wid: str) -> None:
    """Show message history."""
    await query.answer()
    if query.message:
        await send_history(query.message, wid, agent_ctx=ctx)


async def _handle_screenshot(
    query: CallbackQuery,
    ctx: AgentContext,
    wid: str,
) -> None:
    """Capture and send terminal screenshot."""
    from ..screenshot import text_to_image

    await query.answer()

    text = await ctx.tmux_manager.capture_pane(wid, with_ansi=True)
    if not text:
        if query.message:
            await safe_reply(query.message, "❌ Failed to capture pane content.")
        return

    png_bytes = await text_to_image(text, with_ansi=True)

    # Build screenshot keyboard (same as bot.py _build_screenshot_keyboard)
    def btn(label: str, key_id: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{CB_KEYS_PREFIX}{key_id}:{wid}"[:64],
        )

    keyboard = InlineKeyboardMarkup(
        [
            [btn("␣ Space", "spc"), btn("↑", "up"), btn("⇥ Tab", "tab")],
            [btn("←", "lt"), btn("↓", "dn"), btn("→", "rt")],
            [btn("⎋ Esc", "esc"), btn("^C", "cc"), btn("⏎ Enter", "ent")],
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"{CB_SCREENSHOT_REFRESH}{wid}"[:64],
                )
            ],
        ]
    )

    if query.message:
        await query.message.reply_document(
            document=io.BytesIO(png_bytes),
            filename="screenshot.png",
            reply_markup=keyboard,
        )


async def _handle_restart(query: CallbackQuery, ctx: AgentContext, wid: str) -> None:
    """Kill & restart Claude process."""
    display = ctx.session_manager.get_display_name(wid)
    await query.answer("🔄 Restarting...")

    success = await ctx.tmux_manager.restart_claude(wid)
    clear_window_health(wid)
    ctx.session_manager.clear_window_session(wid)

    if query.message:
        if success:
            await safe_reply(query.message, f"✅ Claude restarted in *{display}*.")
        else:
            await safe_reply(
                query.message, f"❌ Failed to restart Claude in *{display}*."
            )


async def _handle_rebuild(
    query: CallbackQuery,
    ctx: AgentContext,
    wid: str,
) -> None:
    """Rebuild CLAUDE.md for workspace."""
    from ..workspace.assembler import ClaudeMdAssembler

    await query.answer()

    state = ctx.session_manager.get_window_state(wid)
    if not state.cwd:
        if query.message:
            await safe_reply(query.message, "❌ Cannot resolve workspace path.")
        return

    workspace_dir = Path(state.cwd)
    if not workspace_dir.is_dir():
        if query.message:
            await safe_reply(query.message, "❌ Workspace directory not found.")
        return

    assembler = ClaudeMdAssembler(
        ctx.config.shared_dir, workspace_dir, locale=ctx.config.locale
    )
    assembler.write()

    if query.message:
        await safe_reply(
            query.message,
            "✅ CLAUDE.md rebuilt. Send /clear to apply new settings.",
        )


async def _handle_cron(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show cron job list (default /cron behavior)."""
    from .cron_handler import format_schedule

    await query.answer()
    if not query.message:
        return

    ctx = _ctx(context)
    rk = ctx.router.extract_routing_key(update)
    if rk is None:
        await safe_reply(query.message, "❌ No workspace for this topic.")
        return
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        await safe_reply(query.message, "❌ No workspace for this topic.")
        return

    display_name = ctx.session_manager.get_display_name(wid)
    agent_prefix = f"{ctx.config.name}/"
    ws_name = display_name.removeprefix(agent_prefix)

    cron_svc = ctx.cron_service
    if not cron_svc:
        await safe_reply(query.message, "❌ Cron service not available.")
        return

    import time

    jobs = await cron_svc.list_jobs(ws_name)
    if not jobs:
        await safe_reply(query.message, "⏰ No scheduled jobs for this workspace.")
        return

    lines = [f"⏰ Cron Jobs ({len(jobs)})\n"]
    for i, job in enumerate(jobs, 1):
        status_icon = "✅" if job.enabled else "⏸️"
        system_tag = " [system]" if job.system else ""
        lines.append(f"**{i}. {job.name}** `{job.id}` [{status_icon}]{system_tag}")
        lines.append(f"   {format_schedule(job.schedule)}")

        if job.state.next_run_at and job.enabled:
            remaining = job.state.next_run_at - time.time()
            if remaining > 0:
                mins, secs = divmod(int(remaining), 60)
                hours, mins = divmod(mins, 60)
                if hours > 0:
                    lines.append(f"   Next: {hours}h {mins}m")
                else:
                    lines.append(f"   Next: {mins}m")
            else:
                lines.append("   Next: imminent")
        elif not job.enabled:
            lines.append("   Next: —")

        lines.append("")

    lines.append("💡 `/cron run <id>` to trigger a job immediately")
    await safe_reply(query.message, "\n".join(lines))


async def _handle_verbosity(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show verbosity setting with inline keyboard."""
    from .callback_data import CB_VERBOSITY

    await query.answer()
    if not query.message:
        return

    user = update.effective_user
    if not user:
        return

    ctx = _ctx(context)
    thread_id = getattr(query.message, "message_thread_id", None) or 0
    current = ctx.session_manager.get_verbosity(user.id, thread_id)

    levels = {
        "quiet": "🔇 Quiet — only final replies",
        "normal": "🔉 Normal — replies + tool summaries",
        "verbose": "🔊 Verbose — everything",
    }

    text = f"📊 *Verbosity*: {current}\n\n{levels.get(current, current)}"

    buttons = []
    for level in ("quiet", "normal", "verbose"):
        label = f"{'✓ ' if level == current else ''}{level}"
        buttons.append(
            InlineKeyboardButton(
                label,
                callback_data=f"{CB_VERBOSITY}{thread_id}:{level}",
            )
        )
    keyboard = InlineKeyboardMarkup([buttons])
    await safe_reply(query.message, text, reply_markup=keyboard)


async def _handle_ls(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show file browser (default /ls behavior)."""
    from .file_browser import (
        LS_ENTRIES_KEY,
        LS_PATH_KEY,
        LS_ROOT_KEY,
        build_file_browser,
    )

    await query.answer()
    if not query.message:
        return

    ctx = _ctx(context)
    rk = ctx.router.extract_routing_key(update)
    if rk is None:
        await safe_reply(query.message, "❌ No workspace for this topic.")
        return
    wid = ctx.router.get_window(rk, ctx)
    if not wid:
        await safe_reply(query.message, "❌ No workspace for this topic.")
        return

    state = ctx.session_manager.get_window_state(wid)
    if not state.cwd:
        await safe_reply(query.message, "❌ Cannot resolve workspace path.")
        return

    workspace_dir = Path(state.cwd)
    root = str(workspace_dir)
    current = root

    text, keyboard, entries = build_file_browser(current, page=0, root_path=root)
    ud = context.user_data
    if ud is not None:
        ud[LS_PATH_KEY] = current
        ud[LS_ROOT_KEY] = root
        ud[LS_ENTRIES_KEY] = entries

    await safe_reply(query.message, text, reply_markup=keyboard)


async def _handle_agentsoul(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show agent soul (default /agentsoul behavior)."""
    from ..persona.agentsoul import read_agentsoul_with_source, read_identity

    await query.answer()
    if not query.message:
        return

    ctx = _ctx(context)
    cfg = ctx.config

    # Resolve workspace for this topic
    rk = ctx.router.extract_routing_key(update)
    ws_dir = None
    if rk:
        wid = ctx.router.get_window(rk, ctx)
        if wid:
            display_name = ctx.session_manager.get_display_name(wid)
            agent_prefix = f"{cfg.name}/"
            ws_name = display_name.removeprefix(agent_prefix)
            ws_dir = cfg.workspace_dir_for(ws_name)

    content, source = read_agentsoul_with_source(cfg.shared_dir, ws_dir)
    if content:
        identity = read_identity(cfg.shared_dir, ws_dir)
        source_label = "📌 workspace 專用" if source == "local" else "🌐 共用"
        await safe_reply(
            query.message,
            f"🪪 {identity.emoji} **{identity.name}** — {identity.role}\n"
            f"Vibe: {identity.vibe}\n"
            f"Source: {source_label}\n\n"
            f"---\n\n"
            f"{content}\n\n"
            f"Use `/agentsoul set <field> <value>` to modify identity fields\n"
            f"Use `/agentsoul edit` to overwrite the entire file",
        )
    else:
        await safe_reply(query.message, "❌ No AGENTSOUL.md found.")


async def _handle_profile(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show user profile (default /profile behavior)."""
    from ..persona.profile import ensure_user_profile, read_user_profile

    await query.answer()
    if not query.message:
        return

    user = update.effective_user
    if not user:
        return

    ctx = _ctx(context)
    users_dir = ctx.config.users_dir

    first_name = user.first_name or ""
    username = user.username or ""
    ensure_user_profile(users_dir, user.id, first_name, username)

    profile = read_user_profile(users_dir, user.id)
    await safe_reply(
        query.message,
        f"👤 **Profile** (`{user.id}`)\n\n"
        f"Name: {profile.name}\n"
        f"Telegram: {profile.telegram or '(none)'}\n"
        f"Timezone: {profile.timezone}\n"
        f"Language: {profile.language}\n"
        f"Notes: {profile.notes or '(none)'}\n\n"
        f"Use `/profile set <field> <value>` to modify",
    )
