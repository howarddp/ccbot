"""Telegram handler for /agentsoul command.

Provides read/edit operations for AGENTSOUL.md (merged personality + identity)
through Telegram bot commands. Edit mode accepts the next message as new content.

Supports per-workspace overrides via copy-on-write: when used in a topic bound
to a workspace, edits are written to that workspace's own AGENTSOUL.md.

Key functions: agentsoul_command().
"""

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..handlers.message_sender import safe_reply
from ..handlers.workspace_resolver import resolve_workspace_for_update
from ..persona.agentsoul import (
    read_agentsoul_with_source,
    read_identity,
    update_identity,
    write_agentsoul,
)
from ..handlers.important_handler import cancel_important_edit
from ..workspace.assembler import ClaudeMdAssembler, rebuild_all_workspaces

logger = logging.getLogger(__name__)

# Track users in edit mode: user_id -> edit_target ("agentsoul")
_edit_mode: dict[int, str] = {}

# Workspace dir captured when edit mode starts: user_id -> workspace_dir or None
_edit_workspace: dict[int, Path | None] = {}


def _cfg(context: ContextTypes.DEFAULT_TYPE):
    """Get AgentConfig from context."""
    return context.bot_data["agent_ctx"].config


def _resolve_workspace_for_thread(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Path | None:
    """Resolve the workspace directory for the current topic."""
    return resolve_workspace_for_update(update, context)


def _rebuild_after_edit(cfg, workspace_dir: Path | None) -> None:  # type: ignore[no-untyped-def]
    """Rebuild CLAUDE.md after an AGENTSOUL.md edit.

    If *workspace_dir* is known, only rebuild that single workspace.
    Otherwise fall back to rebuilding all workspaces.
    """
    if workspace_dir is not None:
        assembler = ClaudeMdAssembler(
            cfg.shared_dir,
            workspace_dir,
            locale=cfg.locale,
            allowed_users=cfg.allowed_users,
        )
        assembler.write()
    else:
        rebuild_all_workspaces(
            cfg.shared_dir,
            cfg.iter_workspace_dirs(),
            locale=cfg.locale,
            allowed_users=cfg.allowed_users,
        )


async def agentsoul_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agentsoul command — view, edit, or set identity fields.

    Usage:
        /agentsoul              — show full AGENTSOUL.md with formatted identity
        /agentsoul set <f> <v>  — update an identity field (name/emoji/role/vibe)
        /agentsoul edit         — enter edit mode, next message overwrites AGENTSOUL.md
    """
    user = update.effective_user
    if not user or not update.message:
        return

    cfg = _cfg(context)
    ws_dir = _resolve_workspace_for_thread(update, context)
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=3)

    # /agentsoul edit
    if len(parts) >= 2 and parts[1].strip().lower() == "edit":
        _edit_mode[user.id] = "agentsoul"
        _edit_workspace[user.id] = ws_dir
        await safe_reply(
            update.message,
            "✏️ Send the new AGENTSOUL.md content. "
            "Your next message will overwrite the entire file.\n"
            "Send /cancel to cancel.",
        )
        return

    # /agentsoul set <field> <value>
    if len(parts) >= 4 and parts[1].lower() == "set":
        field = parts[2].lower()
        value = parts[3]

        field_map = {"name": "name", "emoji": "emoji", "role": "role", "vibe": "vibe"}
        if field not in field_map:
            await safe_reply(
                update.message,
                f"❌ Unknown field: {field}\nAvailable fields: name, emoji, role, vibe",
            )
            return

        updated = update_identity(
            cfg.shared_dir, workspace_dir=ws_dir, **{field_map[field]: value}
        )
        _rebuild_after_edit(cfg, ws_dir)
        source_label = "📌 workspace-local" if ws_dir else "🌐 shared"
        await safe_reply(
            update.message,
            f"✅ Updated {field} = {value} ({source_label})\n\n"
            f"🪪 {updated.emoji} **{updated.name}** — {updated.role}\n"
            f"Vibe: {updated.vibe}",
        )
        return

    # Show current agentsoul
    content, source = read_agentsoul_with_source(cfg.shared_dir, ws_dir)
    if content:
        identity = read_identity(cfg.shared_dir, ws_dir)
        source_label = "📌 workspace-local" if source == "local" else "🌐 shared"
        await safe_reply(
            update.message,
            f"🪪 {identity.emoji} **{identity.name}** — {identity.role}\n"
            f"Vibe: {identity.vibe}\n"
            f"Source: {source_label}\n\n"
            f"---\n\n"
            f"{content}\n\n"
            f"Use `/agentsoul set <field> <value>` to modify identity fields\n"
            f"Use `/agentsoul edit` to overwrite the entire file",
        )
    else:
        await safe_reply(update.message, "🫀 AGENTSOUL.md is not configured yet.")


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

    ws_dir = _edit_workspace.pop(user.id, None)
    text = update.message.text.strip()

    if text.lower() == "/cancel":
        await safe_reply(update.message, "❌ Edit cancelled.")
        return True

    if target == "agentsoul":
        cfg = _cfg(context)
        write_agentsoul(cfg.shared_dir, text, workspace_dir=ws_dir)
        _rebuild_after_edit(cfg, ws_dir)
        source_label = "📌 workspace-local" if ws_dir else "🌐 shared"
        await safe_reply(update.message, f"✅ AGENTSOUL.md updated! ({source_label})")
        return True

    return False


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel — exit edit mode if active."""
    user = update.effective_user
    if not user or not update.message:
        return

    cancelled = False
    if user.id in _edit_mode:
        del _edit_mode[user.id]
        _edit_workspace.pop(user.id, None)
        cancelled = True
    if cancel_important_edit(user.id):
        cancelled = True

    if cancelled:
        await safe_reply(update.message, "❌ Edit cancelled.")
    else:
        await safe_reply(update.message, "No operation in progress.")
