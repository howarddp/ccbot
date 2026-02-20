"""Telegram handlers for /memory and /forget commands.

Provides listing, viewing, searching, and deleting memory files
through Telegram bot commands. Memory is per-topic (each topic has
its own workspace with its own memory directory).

Key functions: memory_command(), forget_command().
"""

import logging
from datetime import date
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..handlers.message_sender import safe_reply
from ..memory.manager import MemoryManager

logger = logging.getLogger(__name__)


def _resolve_workspace_for_thread(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Path | None:
    """Resolve the workspace directory for the current routing context.

    Uses the router to extract routing key and look up bound window.
    Returns None if no bound window / no workspace.
    """
    agent_ctx = context.bot_data["agent_ctx"]
    rk = agent_ctx.router.extract_routing_key(update)
    if rk is None:
        return None

    wid = agent_ctx.router.get_window(rk, agent_ctx)
    if not wid:
        return None

    display_name = agent_ctx.session_manager.get_display_name(wid)
    return agent_ctx.config.workspace_dir_for(display_name)


def _get_memory_manager(workspace_dir: Path) -> MemoryManager:
    """Create a MemoryManager for the given workspace."""
    return MemoryManager(workspace_dir)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory command — list, view, or search memories."""
    user = update.effective_user
    if not user or not update.message:
        return

    workspace_dir = _resolve_workspace_for_thread(update, context)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ No workspace for this topic.")
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=2)
    mm = _get_memory_manager(workspace_dir)

    # /memory today
    if len(parts) >= 2 and parts[1].lower() == "today":
        today_str = date.today().isoformat()
        content = mm.get_daily(today_str)
        if content:
            await safe_reply(update.message, f"📝 **{today_str}**\n\n{content}")
        else:
            await safe_reply(update.message, f"📝 No memories for today ({today_str}).")
        return

    # /memory search <query>
    if len(parts) >= 3 and parts[1].lower() == "search":
        query = parts[2]
        results = mm.search(query)
        if not results:
            await safe_reply(update.message, f'🔍 No results for "{query}".')
            return

        lines = [f'🔍 Search "{query}" — {len(results)} results\n']
        for r in results[:20]:  # Limit to 20 results
            lines.append(f"📄 `{r.file}:{r.line_num}` {r.line}")

        if len(results) > 20:
            lines.append(f"\n…{len(results) - 20} more results")

        await safe_reply(update.message, "\n".join(lines))
        return

    # /memory <date> — view specific date
    if len(parts) >= 2:
        date_str = parts[1]
        content = mm.get_daily(date_str)
        if content:
            await safe_reply(update.message, f"📝 **{date_str}**\n\n{content}")
        else:
            await safe_reply(update.message, f"📝 No memories found for {date_str}.")
        return

    # /memory — list recent memories
    cfg = context.bot_data["agent_ctx"].config
    memories = mm.list_daily(days=cfg.recent_memory_days)
    if not memories:
        await safe_reply(update.message, "📝 No daily memories yet.")
        return

    lines = ["📝 **Recent Memories**\n"]
    for m in memories:
        lines.append(f"• `{m.date}` — {m.preview}")

    lines.append(f"\n{len(memories)} total | Use `/memory <date>` to view details")
    await safe_reply(update.message, "\n".join(lines))


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /forget command — delete daily memory files."""
    user = update.effective_user
    if not user or not update.message:
        return

    workspace_dir = _resolve_workspace_for_thread(update, context)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ No workspace for this topic.")
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    mm = _get_memory_manager(workspace_dir)

    if len(parts) < 2:
        await safe_reply(
            update.message,
            "❓ Usage:\n"
            "• `/forget 2026-02-15` — delete a specific date\n"
            "• `/forget all` — clear all daily memories (keeps MEMORY.md)",
        )
        return

    target = parts[1].strip()

    # /forget all
    if target.lower() == "all":
        count = mm.delete_all_daily()
        await safe_reply(update.message, f"🗑️ Deleted {count} daily memories.")
        return

    # /forget <date>
    if mm.delete_daily(target):
        await safe_reply(update.message, f"🗑️ Deleted memory for {target}.")
    else:
        await safe_reply(update.message, f"❌ No memory found for {target}.")
