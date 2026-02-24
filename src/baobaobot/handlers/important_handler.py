"""Handler for Important Instructions — workspace-local additions to built-in rules.

Workspace additions are stored in .persona/important.md and appended after the
built-in rules in the assembled CLAUDE.md.

Key functions: view_important(), start_important_edit(), handle_important_edit_message().
"""

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..handlers.message_sender import safe_reply
from ..workspace.assembler import ClaudeMdAssembler

logger = logging.getLogger(__name__)

# Track users in edit mode: user_id -> True
_edit_mode: dict[int, bool] = {}

# Workspace dir captured when edit mode starts
_edit_workspace: dict[int, Path | None] = {}


def get_important_path(workspace_dir: Path) -> Path:
    return workspace_dir / ".persona" / "important.md"


def read_workspace_important(workspace_dir: Path | None) -> str:
    """Read workspace-local important instructions, stripping frontmatter."""
    if workspace_dir is None:
        return ""
    path = get_important_path(workspace_dir)
    try:
        from baobaobot.memory.utils import strip_frontmatter
        content = path.read_text(encoding="utf-8").strip()
        return strip_frontmatter(content).strip()
    except OSError:
        return ""



def cancel_important_edit(user_id: int) -> bool:
    """Cancel important edit mode for a user.  Returns True if mode was active."""
    if user_id in _edit_mode:
        del _edit_mode[user_id]
        _edit_workspace.pop(user_id, None)
        return True
    return False


async def view_important(
    query_message,  # Message to reply to
    workspace_dir: Path | None,
) -> None:
    """Display workspace-local important instructions."""
    workspace_content = read_workspace_important(workspace_dir)

    if workspace_content:
        lines = ["📋 **Important Instructions** (workspace)\n", workspace_content]
    else:
        lines = ["📋 **Important Instructions**\n", "_No workspace instructions yet._"]

    lines.append(
        "\n\nUse `/config` > Important > Edit to set workspace-specific instructions."
    )
    await safe_reply(query_message, "\n".join(lines))


async def start_important_edit(
    query_message,
    user_id: int,
    workspace_dir: Path | None,
) -> None:
    """Enter edit mode for workspace important instructions."""
    _edit_mode[user_id] = True
    _edit_workspace[user_id] = workspace_dir

    current = read_workspace_important(workspace_dir)
    if current:
        hint = f"Current content:\n```\n{current}\n```\n\n"
    else:
        hint = "No workspace instructions yet.\n\n"

    await safe_reply(
        query_message,
        f"✏️ {hint}"
        "Send the new workspace instructions. "
        "Your next message will **overwrite** the current content.\n"
        "Send /cancel to cancel.",
    )


async def handle_important_edit_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Check if user is in important edit mode and handle their message.

    Returns True if the message was consumed, False otherwise.
    """
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return False

    active = _edit_mode.pop(user.id, None)
    if not active:
        return False

    ws_dir = _edit_workspace.pop(user.id, None)
    text = update.message.text.strip()

    if text.lower() == "/cancel":
        await safe_reply(update.message, "❌ Edit cancelled.")
        return True

    # Write workspace additions
    if ws_dir is not None:
        path = get_important_path(ws_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

        # Rebuild CLAUDE.md
        cfg = context.bot_data["agent_ctx"].config
        assembler = ClaudeMdAssembler(
            cfg.shared_dir,
            ws_dir,
            locale=cfg.locale,
            allowed_users=cfg.allowed_users,
        )
        assembler.write()
        await safe_reply(update.message, "✅ Important instructions updated! (workspace-local)")
    else:
        await safe_reply(
            update.message,
            "❌ No workspace bound to this topic. Cannot save.",
        )

    return True
