"""Telegram handler for /cron command — manage per-workspace scheduled jobs.

Usage:
  /cron                              — list jobs for this topic
  /cron add <schedule> <message>     — add a cron job
  /cron remove <id>                  — remove a job
  /cron enable <id>                  — enable a job
  /cron disable <id>                 — disable a job
  /cron run <id>                     — trigger immediately
  /cron status                       — show cron service status
"""

import logging
import time
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..cron.parse import format_schedule, parse_schedule
from ..cron.service import cron_service
from ..cron.types import WorkspaceMeta
from ..handlers.message_sender import safe_reply
from ..session import session_manager

logger = logging.getLogger(__name__)


def _resolve_workspace_for_thread(update: Update) -> tuple[Path | None, str]:
    """Resolve workspace dir and name for the current thread.

    Returns (workspace_dir, workspace_name) or (None, "") if unresolvable.
    """
    user = update.effective_user
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not user or not msg:
        return None, ""

    thread_id = getattr(msg, "message_thread_id", None)
    if thread_id is None or thread_id == 1:
        return None, ""

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if not wid:
        return None, ""

    display_name = session_manager.get_display_name(wid)
    return config.workspace_dir_for(display_name), display_name


def _get_workspace_meta(update: Update) -> WorkspaceMeta:
    """Extract workspace meta from the update for window recreation."""
    user = update.effective_user
    msg = update.message
    meta = WorkspaceMeta()
    if user:
        meta.user_id = user.id
    if msg:
        meta.thread_id = getattr(msg, "message_thread_id", 0) or 0
        if msg.chat:
            meta.chat_id = msg.chat.id
    return meta


async def cron_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cron command — dispatch to subcommands."""
    user = update.effective_user
    if not user or not update.message:
        return

    workspace_dir, ws_name = _resolve_workspace_for_thread(update)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ 此 topic 尚無 workspace。")
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=2)

    # /cron — list
    if len(parts) <= 1:
        await _cmd_list(update, ws_name)
        return

    sub = parts[1].lower()

    if sub == "add" and len(parts) >= 3:
        await _cmd_add(update, ws_name, parts[2])
    elif sub == "remove" and len(parts) >= 3:
        await _cmd_remove(update, ws_name, parts[2].strip())
    elif sub == "enable" and len(parts) >= 3:
        await _cmd_enable(update, ws_name, parts[2].strip())
    elif sub == "disable" and len(parts) >= 3:
        await _cmd_disable(update, ws_name, parts[2].strip())
    elif sub == "run" and len(parts) >= 3:
        await _cmd_run(update, ws_name, parts[2].strip())
    elif sub == "status":
        await _cmd_status(update)
    else:
        await safe_reply(
            update.message,
            "❓ 用法:\n"
            '• `/cron add "0 9 * * *" 早安！`\n'
            "• `/cron add every:30m 檢查信箱`\n"
            "• `/cron add at:2026-02-20T14:00 提醒開會`\n"
            "• `/cron remove <id>`\n"
            "• `/cron enable <id>` / `disable <id>`\n"
            "• `/cron run <id>`\n"
            "• `/cron status`",
        )


async def _cmd_list(update: Update, ws_name: str) -> None:
    """List all cron jobs for this workspace."""
    assert update.message
    jobs = await cron_service.list_jobs(ws_name)
    if not jobs:
        await safe_reply(update.message, "⏰ 此 workspace 尚無排程任務。")
        return

    lines = [f"⏰ Cron Jobs ({len(jobs)})\n"]
    for i, job in enumerate(jobs, 1):
        status_icon = "✅" if job.enabled else "⏸️"
        lines.append(f"**{i}. {job.name}** `{job.id}` [{status_icon}]")
        lines.append(f"   {format_schedule(job.schedule)}")

        # Next run
        if job.state.next_run_at and job.enabled:
            remaining = job.state.next_run_at - time.time()
            if remaining > 0:
                next_str = _format_relative_time(remaining)
                lines.append(f"   Next: {next_str}")
            else:
                lines.append("   Next: imminent")
        elif not job.enabled:
            lines.append("   Next: —")

        # Last run
        if job.state.last_run_at:
            ago = time.time() - job.state.last_run_at
            ago_str = _format_relative_time(ago)
            status = job.state.last_status
            if status == "error":
                lines.append(f"   Last: ❌ {status} ({ago_str} ago)")
            else:
                dur = (
                    f"{job.state.last_duration_s:.0f}s"
                    if job.state.last_duration_s
                    else ""
                )
                lines.append(f"   Last: {status} {dur} ({ago_str} ago)")

        if job.state.last_error:
            lines.append(f"   Error: `{job.state.last_error}`")
        lines.append("")

    await safe_reply(update.message, "\n".join(lines))


async def _cmd_add(update: Update, ws_name: str, args: str) -> None:
    """Add a new cron job."""
    assert update.message
    # Parse: first token is schedule, rest is message
    # For quoted cron expressions: "0 9 * * *" message here
    schedule_str, message = _split_schedule_and_message(args)
    if not message:
        await safe_reply(
            update.message,
            '❌ 請提供排程和訊息。\n例：`/cron add "0 9 * * *" 早安！整理待辦事項`',
        )
        return

    schedule, err = parse_schedule(schedule_str)
    if not schedule:
        # Detect likely unquoted cron expression (first token is a number or */number)
        hint = ""
        first_token = schedule_str.split()[0] if schedule_str else ""
        if first_token and (first_token.isdigit() or first_token.startswith("*")):
            hint = '\n💡 Cron 表達式需加引號：`/cron add "0 9 * * *" 訊息`'
        await safe_reply(update.message, f"❌ 排程格式錯誤: {err}{hint}")
        return

    # Generate a short name from message
    name = message[:20].replace("\n", " ").strip()

    meta = _get_workspace_meta(update)
    job = await cron_service.add_job(ws_name, name, schedule, message, meta=meta)

    next_info = ""
    if job.state.next_run_at:
        remaining = job.state.next_run_at - time.time()
        if remaining > 0:
            next_info = f"\nNext run: {_format_relative_time(remaining)}"

    await safe_reply(
        update.message,
        f"✅ 排程已建立\n"
        f"ID: `{job.id}`\n"
        f"Schedule: {format_schedule(job.schedule)}{next_info}",
    )


async def _cmd_remove(update: Update, ws_name: str, job_id: str) -> None:
    assert update.message
    ok = await cron_service.remove_job(ws_name, job_id)
    if ok:
        await safe_reply(update.message, f"🗑️ 已移除排程 `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ 找不到排程 `{job_id}`")


async def _cmd_enable(update: Update, ws_name: str, job_id: str) -> None:
    assert update.message
    job = await cron_service.enable_job(ws_name, job_id)
    if job:
        await safe_reply(update.message, f"✅ 已啟用排程 `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ 找不到排程 `{job_id}`")


async def _cmd_disable(update: Update, ws_name: str, job_id: str) -> None:
    assert update.message
    job = await cron_service.disable_job(ws_name, job_id)
    if job:
        await safe_reply(update.message, f"⏸️ 已停用排程 `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ 找不到排程 `{job_id}`")


async def _cmd_run(update: Update, ws_name: str, job_id: str) -> None:
    assert update.message
    ok = await cron_service.run_job_now(ws_name, job_id)
    if ok:
        await safe_reply(update.message, f"▶️ 已觸發排程 `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ 找不到排程 `{job_id}`")


async def _cmd_status(update: Update) -> None:
    assert update.message
    running = "✅ Running" if cron_service.is_running else "❌ Stopped"
    await safe_reply(
        update.message,
        f"⏰ Cron Service: {running}\n"
        f"Workspaces: {cron_service.workspace_count}\n"
        f"Total jobs: {cron_service.total_jobs}",
    )


# --- Helpers ---


def _split_schedule_and_message(args: str) -> tuple[str, str]:
    """Split schedule string from message in user input.

    Handles quoted cron expressions: "0 9 * * *" message
    And unquoted: every:30m message
    """
    args = args.strip()

    # Quoted cron expression
    if args.startswith('"') or args.startswith("'"):
        quote = args[0]
        end = args.find(quote, 1)
        if end > 0:
            schedule = args[1:end]
            message = args[end + 1 :].strip()
            return schedule, message

    # Unquoted: first token is schedule
    parts = args.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return args, ""


def _format_relative_time(seconds: float) -> str:
    """Format seconds into human-readable relative time."""
    seconds = abs(seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        return f"{h}h{m}m" if m else f"{h}h"
    d = int(seconds / 86400)
    return f"{d}d"
