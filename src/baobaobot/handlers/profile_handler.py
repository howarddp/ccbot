"""Telegram handler for /profile command.

Provides read/update operations for per-user profiles in shared_dir/users/.
Each user can only view and edit their own profile.

Key function: profile_command().
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..handlers.message_sender import safe_reply
from ..persona.profile import (
    ensure_user_profile,
    read_user_profile,
    update_user_profile,
)

logger = logging.getLogger(__name__)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /profile command — view or update the caller's own profile."""
    user = update.effective_user
    if not user or not update.message:
        return

    users_dir = context.bot_data["agent_ctx"].config.users_dir

    # Ensure profile exists
    first_name = user.first_name or ""
    username = user.username or ""
    ensure_user_profile(users_dir, user.id, first_name, username)

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=3)

    # /profile set <field> <value>
    if len(parts) >= 4 and parts[1].lower() == "set":
        field = parts[2].lower()
        value = parts[3]

        field_map = {
            "name": "name",
            "telegram": "telegram",
            "tz": "timezone",
            "timezone": "timezone",
            "lang": "language",
            "language": "language",
            "notes": "notes",
        }

        if field not in field_map:
            await safe_reply(
                update.message,
                f"❌ Unknown field: {field}\nAvailable fields: name, telegram, tz, lang, notes",
            )
            return

        updated = update_user_profile(users_dir, user.id, **{field_map[field]: value})
        await safe_reply(
            update.message,
            f"✅ Updated {field} = {value}\n\n"
            f"👤 **{updated.name}** {updated.telegram}\n"
            f"🕐 {updated.timezone} | 🗣️ {updated.language}",
        )
        return

    # Show current profile
    profile = read_user_profile(users_dir, user.id)
    await safe_reply(
        update.message,
        f"👤 **Profile** (`{user.id}`)\n\n"
        f"Name: {profile.name}\n"
        f"Telegram: {profile.telegram or '(none)'}\n"
        f"Timezone: {profile.timezone}\n"
        f"Language: {profile.language}\n"
        f"Notes: {profile.notes or '(none)'}\n\n"
        f"Use `/profile set <field> <value>` to modify",
    )
