"""Telegram handler for /profile command.

Provides read/update operations for per-user profiles in shared_dir/users/.
Each user can only view and edit their own profile.

Supports per-workspace overrides via copy-on-write: when used in a topic bound
to a workspace, edits are written to that workspace's .persona/<user_id>.md.

Key function: profile_command().
"""

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..handlers.message_sender import safe_reply
from ..persona.profile import (
    ensure_user_profile,
    read_user_profile_with_source,
    write_user_profile,
)
from ..workspace.assembler import ClaudeMdAssembler

logger = logging.getLogger(__name__)


def _resolve_workspace_for_thread(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Path | None:
    """Resolve the workspace directory for the current topic."""
    agent_ctx = context.bot_data["agent_ctx"]
    rk = agent_ctx.router.extract_routing_key(update)
    if rk is None:
        return None

    wid = agent_ctx.router.get_window(rk, agent_ctx)
    if not wid:
        return None

    display_name = agent_ctx.session_manager.get_display_name(wid)
    agent_prefix = f"{agent_ctx.config.name}/"
    ws_name = display_name.removeprefix(agent_prefix)
    return agent_ctx.config.workspace_dir_for(ws_name)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /profile command — view or update the caller's own profile."""
    user = update.effective_user
    if not user or not update.message:
        return

    cfg = context.bot_data["agent_ctx"].config
    users_dir = cfg.users_dir
    ws_dir = _resolve_workspace_for_thread(update, context)

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

        updated = write_user_profile(
            users_dir, user.id, workspace_dir=ws_dir, **{field_map[field]: value}
        )
        source_label = "📌 workspace-local" if ws_dir else "🌐 shared"

        # Rebuild CLAUDE.md so embedded profiles stay in sync
        target_dir = ws_dir if ws_dir and ws_dir.is_dir() else None
        if target_dir:
            assembler = ClaudeMdAssembler(
                cfg.shared_dir,
                target_dir,
                locale=cfg.locale,
                allowed_users=cfg.allowed_users,
            )
            assembler.write()

        await safe_reply(
            update.message,
            f"✅ Updated {field} = {value} ({source_label})\n\n"
            f"👤 **{updated.name}** {updated.telegram}\n"
            f"🕐 {updated.timezone} | 🗣️ {updated.language}",
        )
        return

    # Show current profile
    profile, source = read_user_profile_with_source(users_dir, user.id, ws_dir)
    source_label = "📌 workspace-local" if source == "local" else "🌐 shared"
    await safe_reply(
        update.message,
        f"👤 **Profile** (`{user.id}`) — {source_label}\n\n"
        f"Name: {profile.name}\n"
        f"Telegram: {profile.telegram or '(none)'}\n"
        f"Timezone: {profile.timezone}\n"
        f"Language: {profile.language}\n"
        f"Notes: {profile.notes or '(none)'}\n\n"
        f"Use `/profile set <field> <value>` to modify",
    )
