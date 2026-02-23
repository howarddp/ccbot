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

from ..cron.parse import format_schedule, parse_schedule
from ..cron.types import WorkspaceMeta
from ..handlers.message_sender import safe_reply

logger = logging.getLogger(__name__)


def _resolve_workspace_for_thread(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[Path | None, str]:
    """Resolve workspace dir and name for the current routing context.

    Uses the router to extract routing key and look up bound window.
    Returns (workspace_dir, workspace_name) or (None, "") if unresolvable.
    """
    agent_ctx = context.bot_data["agent_ctx"]
    rk = agent_ctx.router.extract_routing_key(update)
    if rk is None:
        return None, ""

    wid = agent_ctx.router.get_window(rk, agent_ctx)
    if not wid:
        return None, ""

    display_name = agent_ctx.session_manager.get_display_name(wid)
    # Strip agent prefix (e.g. "tecoailab/O2O" → "O2O") for workspace resolution
    agent_prefix = f"{agent_ctx.config.name}/"
    ws_name = display_name.removeprefix(agent_prefix)
    return agent_ctx.config.workspace_dir_for(ws_name), ws_name


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

    workspace_dir, ws_name = _resolve_workspace_for_thread(update, context)
    if workspace_dir is None:
        await safe_reply(update.message, "❌ No workspace for this topic.")
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=2)

    cron_svc = context.bot_data["agent_ctx"].cron_service

    # /cron — list
    if len(parts) <= 1:
        await _cmd_list(update, ws_name, cron_svc)
        return

    sub = parts[1].lower()

    if sub == "add" and len(parts) >= 3:
        await _cmd_add(update, ws_name, parts[2], cron_svc)
    elif sub == "remove" and len(parts) >= 3:
        await _cmd_remove(update, ws_name, parts[2].strip(), cron_svc)
    elif sub == "enable" and len(parts) >= 3:
        await _cmd_enable(update, ws_name, parts[2].strip(), cron_svc)
    elif sub == "disable" and len(parts) >= 3:
        await _cmd_disable(update, ws_name, parts[2].strip(), cron_svc)
    elif sub == "run" and len(parts) >= 3:
        await _cmd_run(update, ws_name, parts[2].strip(), cron_svc)
    elif sub == "status":
        await _cmd_status(update, cron_svc)
    else:
        await safe_reply(
            update.message,
            "❓ Usage:\n"
            '• `/cron add "0 9 * * *" Good morning!`\n'
            "• `/cron add every:30m Check inbox`\n"
            "• `/cron add at:2026-02-20T14:00 Meeting reminder`\n"
            "• `/cron remove <id>`\n"
            "• `/cron enable <id>` / `disable <id>`\n"
            "• `/cron run <id>`\n"
            "• `/cron status`",
        )


async def _cmd_list(update: Update, ws_name: str, cron_svc) -> None:
    """List all cron jobs for this workspace."""
    assert update.message
    jobs = await cron_svc.list_jobs(ws_name)
    if not jobs:
        await safe_reply(update.message, "⏰ No scheduled jobs for this workspace.")
        return

    lines = [f"⏰ Cron Jobs ({len(jobs)})\n"]
    for i, job in enumerate(jobs, 1):
        status_icon = "✅" if job.enabled else "⏸️"
        system_tag = " [system]" if job.system else ""
        lines.append(f"**{i}. {job.name}** `{job.id}` [{status_icon}]{system_tag}")
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

    lines.append("💡 `/cron run <id>` to trigger a job immediately")
    await safe_reply(update.message, "\n".join(lines))


async def _cmd_add(update: Update, ws_name: str, args: str, cron_svc) -> None:
    """Add a new cron job."""
    assert update.message
    # Parse: first token is schedule, rest is message
    # For quoted cron expressions: "0 9 * * *" message here
    schedule_str, message = _split_schedule_and_message(args)
    if not message:
        await safe_reply(
            update.message,
            '❌ Please provide a schedule and message.\nExample: `/cron add "0 9 * * *" Good morning! Review TODOs`',
        )
        return

    schedule, err = parse_schedule(schedule_str)
    if not schedule:
        # Detect likely unquoted cron expression (first token is a number or */number)
        hint = ""
        first_token = schedule_str.split()[0] if schedule_str else ""
        if first_token and (first_token.isdigit() or first_token.startswith("*")):
            hint = '\n💡 Cron expressions need quotes: `/cron add "0 9 * * *" message`'
        await safe_reply(update.message, f"❌ Invalid schedule format: {err}{hint}")
        return

    # Generate a short name from message
    name = message[:20].replace("\n", " ").strip()

    meta = _get_workspace_meta(update)
    creator_id = update.effective_user.id if update.effective_user else 0
    job = await cron_svc.add_job(
        ws_name, name, schedule, message, meta=meta, creator_user_id=creator_id
    )

    next_info = ""
    if job.state.next_run_at:
        remaining = job.state.next_run_at - time.time()
        if remaining > 0:
            next_info = f"\nNext run: {_format_relative_time(remaining)}"

    await safe_reply(
        update.message,
        f"✅ Schedule created\n"
        f"ID: `{job.id}`\n"
        f"Schedule: {format_schedule(job.schedule)}{next_info}",
    )


async def _cmd_remove(update: Update, ws_name: str, job_id: str, cron_svc) -> None:
    assert update.message
    # Block removal of system jobs
    jobs = await cron_svc.list_jobs(ws_name)
    for j in jobs:
        if j.id == job_id and j.system:
            await safe_reply(
                update.message,
                f"❌ System job `{job_id}` cannot be removed. Use `/cron disable {job_id}` to disable it.",
            )
            return
    ok = await cron_svc.remove_job(ws_name, job_id)
    if ok:
        await safe_reply(update.message, f"🗑️ Removed schedule `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ Schedule `{job_id}` not found")


async def _cmd_enable(update: Update, ws_name: str, job_id: str, cron_svc) -> None:
    assert update.message
    job = await cron_svc.enable_job(ws_name, job_id)
    if job:
        await safe_reply(update.message, f"✅ Enabled schedule `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ Schedule `{job_id}` not found")


async def _cmd_disable(update: Update, ws_name: str, job_id: str, cron_svc) -> None:
    assert update.message
    job = await cron_svc.disable_job(ws_name, job_id)
    if job:
        await safe_reply(update.message, f"⏸️ Disabled schedule `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ Schedule `{job_id}` not found")


async def _cmd_run(update: Update, ws_name: str, job_id: str, cron_svc) -> None:
    assert update.message
    ok = await cron_svc.run_job_now(ws_name, job_id)
    if ok:
        await safe_reply(update.message, f"▶️ Triggered schedule `{job_id}`")
    else:
        await safe_reply(update.message, f"❌ Schedule `{job_id}` not found")


async def _cmd_status(update: Update, cron_svc) -> None:
    assert update.message
    running = "✅ Running" if cron_svc.is_running else "❌ Stopped"
    await safe_reply(
        update.message,
        f"⏰ Cron Service: {running}\n"
        f"Workspaces: {cron_svc.workspace_count}\n"
        f"Total jobs: {cron_svc.total_jobs}",
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
