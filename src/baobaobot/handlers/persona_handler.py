"""Telegram handlers for /soul and /identity commands.

Provides read/edit operations for SOUL.md and IDENTITY.md through
Telegram bot commands. Edit mode accepts the next message as new content.

Persona files live in config.shared_dir (shared across all topics).

Key functions: soul_command(), identity_command().
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..handlers.message_sender import safe_reply
from ..persona.identity import read_identity, read_identity_raw, update_identity
from ..persona.soul import read_soul, write_soul
from ..workspace.assembler import rebuild_all_workspaces

logger = logging.getLogger(__name__)

# Track users in edit mode: user_id -> edit_target ("soul")
_edit_mode: dict[int, str] = {}


async def soul_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /soul command — view or edit SOUL.md."""
    user = update.effective_user
    if not user or not update.message:
        return

    text = (update.message.text or "").strip()
    args = text.split(maxsplit=1)

    if len(args) > 1 and args[1].strip().lower() == "edit":
        # Enter edit mode
        _edit_mode[user.id] = "soul"
        await safe_reply(
            update.message,
            "✏️ 請發送新的 SOUL.md 內容。下一則訊息將覆蓋整個 SOUL.md。\n"
            "發送 /cancel 取消。",
        )
        return

    # Show current soul
    content = read_soul(config.shared_dir)
    if content:
        await safe_reply(update.message, f"🫀 **SOUL.md**\n\n{content}")
    else:
        await safe_reply(update.message, "🫀 SOUL.md 尚未設定。")


async def identity_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /identity command — view or update identity fields."""
    user = update.effective_user
    if not user or not update.message:
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=3)

    # /identity set <field> <value>
    if len(parts) >= 4 and parts[1].lower() == "set":
        field = parts[2].lower()
        value = parts[3]

        field_map = {"name": "name", "emoji": "emoji", "role": "role", "vibe": "vibe"}
        if field not in field_map:
            await safe_reply(
                update.message,
                f"❌ 不認識的欄位: {field}\n可用欄位: name, emoji, role, vibe",
            )
            return

        updated = update_identity(config.shared_dir, **{field_map[field]: value})
        rebuild_all_workspaces(
            config.shared_dir, config.iter_workspace_dirs(), config.recent_memory_days
        )
        await safe_reply(
            update.message,
            f"✅ 已更新 {field} = {value}\n\n"
            f"🪪 {updated.emoji} **{updated.name}** — {updated.role}\n"
            f"氛圍: {updated.vibe}",
        )
        return

    # Show current identity
    content = read_identity_raw(config.shared_dir)
    if content:
        identity = read_identity(config.shared_dir)
        await safe_reply(
            update.message,
            f"🪪 **IDENTITY.md**\n\n"
            f"{identity.emoji} **{identity.name}** — {identity.role}\n"
            f"氛圍: {identity.vibe}\n\n"
            f"使用 `/identity set <field> <value>` 修改",
        )
    else:
        await safe_reply(update.message, "🪪 IDENTITY.md 尚未設定。")


async def handle_edit_mode_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Check if user is in edit mode and handle their message.

    Returns:
        True if the message was consumed by edit mode, False otherwise.
    """
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return False

    target = _edit_mode.pop(user.id, None)
    if not target:
        return False

    text = update.message.text.strip()

    if text.lower() == "/cancel":
        await safe_reply(update.message, "❌ 已取消編輯。")
        return True

    if target == "soul":
        write_soul(config.shared_dir, text)
        rebuild_all_workspaces(
            config.shared_dir, config.iter_workspace_dirs(), config.recent_memory_days
        )
        await safe_reply(update.message, "✅ SOUL.md 已更新！")
        return True

    return False


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel — exit edit mode if active."""
    user = update.effective_user
    if not user or not update.message:
        return

    if user.id in _edit_mode:
        del _edit_mode[user.id]
        await safe_reply(update.message, "❌ 已取消編輯。")
    else:
        await safe_reply(update.message, "沒有進行中的操作。")
