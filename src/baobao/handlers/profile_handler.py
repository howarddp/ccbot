"""Telegram handler for /profile command.

Provides read/update operations for USER.md through Telegram bot commands.

Key function: profile_command().
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..handlers.message_sender import safe_reply
from ..persona.profile import read_profile, read_profile_raw, update_profile
from ..workspace.assembler import ClaudeMdAssembler

logger = logging.getLogger(__name__)


def _rebuild_claude_md() -> None:
    """Rebuild CLAUDE.md after a profile change."""
    assembler = ClaudeMdAssembler(config.workspace_dir, config.recent_memory_days)
    assembler.write()


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /profile command — view or update user profile."""
    user = update.effective_user
    if not user or not update.message:
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=3)

    # /profile set <field> <value>
    if len(parts) >= 4 and parts[1].lower() == "set":
        field = parts[2].lower()
        value = parts[3]

        field_map = {
            "name": "name",
            "nickname": "nickname",
            "tz": "timezone",
            "timezone": "timezone",
            "lang": "language",
            "language": "language",
            "notes": "notes",
        }

        if field not in field_map:
            await safe_reply(
                update.message,
                f"❌ 不認識的欄位: {field}\n"
                "可用欄位: name, nickname, tz, lang, notes",
            )
            return

        updated = update_profile(config.workspace_dir, **{field_map[field]: value})
        _rebuild_claude_md()
        await safe_reply(
            update.message,
            f"✅ 已更新 {field} = {value}\n\n"
            f"👤 **{updated.name}** ({updated.nickname})\n"
            f"🕐 {updated.timezone} | 🗣️ {updated.language}",
        )
        return

    # Show current profile
    content = read_profile_raw(config.workspace_dir)
    if content:
        profile = read_profile(config.workspace_dir)
        await safe_reply(
            update.message,
            f"👤 **USER.md**\n\n"
            f"名字: {profile.name}\n"
            f"稱呼: {profile.nickname}\n"
            f"時區: {profile.timezone}\n"
            f"語言: {profile.language}\n"
            f"備註: {profile.notes or '（無）'}\n\n"
            f"使用 `/profile set <field> <value>` 修改",
        )
    else:
        await safe_reply(update.message, "👤 USER.md 尚未設定。")
