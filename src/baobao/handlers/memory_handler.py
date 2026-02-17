"""Telegram handlers for /memory and /forget commands.

Provides listing, viewing, searching, and deleting memory files
through Telegram bot commands.

Key functions: memory_command(), forget_command().
"""

import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..handlers.message_sender import safe_reply
from ..memory.manager import MemoryManager

logger = logging.getLogger(__name__)


def _get_memory_manager() -> MemoryManager:
    """Create a MemoryManager for the configured workspace."""
    return MemoryManager(config.workspace_dir)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory command — list, view, or search memories."""
    user = update.effective_user
    if not user or not update.message:
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=2)
    mm = _get_memory_manager()

    # /memory today
    if len(parts) >= 2 and parts[1].lower() == "today":
        today_str = date.today().isoformat()
        content = mm.get_daily(today_str)
        if content:
            await safe_reply(update.message, f"📝 **{today_str}**\n\n{content}")
        else:
            await safe_reply(update.message, f"📝 今天 ({today_str}) 尚無記憶。")
        return

    # /memory search <query>
    if len(parts) >= 3 and parts[1].lower() == "search":
        query = parts[2]
        results = mm.search(query)
        if not results:
            await safe_reply(update.message, f"🔍 找不到「{query}」的結果。")
            return

        lines = [f"🔍 搜尋「{query}」— {len(results)} 筆結果\n"]
        for r in results[:20]:  # Limit to 20 results
            lines.append(f"📄 `{r.file}:{r.line_num}` {r.line}")

        if len(results) > 20:
            lines.append(f"\n…還有 {len(results) - 20} 筆結果")

        await safe_reply(update.message, "\n".join(lines))
        return

    # /memory <date> — view specific date
    if len(parts) >= 2:
        date_str = parts[1]
        content = mm.get_daily(date_str)
        if content:
            await safe_reply(update.message, f"📝 **{date_str}**\n\n{content}")
        else:
            await safe_reply(update.message, f"📝 找不到 {date_str} 的記憶。")
        return

    # /memory — list recent memories
    memories = mm.list_daily(days=config.recent_memory_days)
    if not memories:
        await safe_reply(update.message, "📝 尚無每日記憶。")
        return

    lines = ["📝 **近期記憶**\n"]
    for m in memories:
        lines.append(f"• `{m.date}` — {m.preview}")

    lines.append(f"\n共 {len(memories)} 筆 | 使用 `/memory <日期>` 查看詳情")
    await safe_reply(update.message, "\n".join(lines))


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /forget command — delete daily memory files."""
    user = update.effective_user
    if not user or not update.message:
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    mm = _get_memory_manager()

    if len(parts) < 2:
        await safe_reply(
            update.message,
            "❓ 用法:\n"
            "• `/forget 2026-02-15` — 刪除特定日期\n"
            "• `/forget all` — 清除所有每日記憶（保留 MEMORY.md）",
        )
        return

    target = parts[1].strip()

    # /forget all
    if target.lower() == "all":
        count = mm.delete_all_daily()
        await safe_reply(update.message, f"🗑️ 已刪除 {count} 筆每日記憶。")
        return

    # /forget <date>
    if mm.delete_daily(target):
        await safe_reply(update.message, f"🗑️ 已刪除 {target} 的記憶。")
    else:
        await safe_reply(update.message, f"❌ 找不到 {target} 的記憶。")
