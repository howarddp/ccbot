"""Per-user message queue management for ordered message delivery.

Provides a queue-based message processing system that ensures:
  - Messages are sent in receive order (FIFO)
  - Status messages always follow content messages
  - Consecutive content messages can be merged for efficiency
  - Rate limiting is respected
  - Thread-aware sending: each MessageTask carries an optional thread_id
    for Telegram topic support

Key components:
  - MessageTask: Dataclass representing a queued message task (with thread_id)
  - get_or_create_queue: Get or create queue and worker for a user
  - Message queue worker: Background task processing user's queue
  - Content task processing with tool_use/tool_result handling
  - Status message tracking and conversion (keyed by (user_id, thread_id))

All per-user state is stored on AgentContext.queue_state to ensure
multi-agent isolation (no module-level globals keyed by user_id).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TimedOut

from ..markdown_v2 import convert_markdown
from ..terminal_parser import parse_status_line
from .message_sender import NO_LINK_PREVIEW, rate_limit_send_message

if TYPE_CHECKING:
    from ..agent_context import AgentContext

logger = logging.getLogger(__name__)

# Merge limit for content messages
MERGE_MAX_LENGTH = 3800  # Leave room for markdown conversion overhead

# Maximum retries for transient network errors in queue worker
_WORKER_MAX_RETRIES = 3
_WORKER_RETRY_DELAY = 5  # seconds


@dataclass
class MessageTask:
    """Message task for queue processing."""

    task_type: Literal["content", "status_update", "status_clear"]
    text: str | None = None
    window_id: str | None = None
    # content type fields
    parts: list[str] = field(default_factory=list)
    tool_use_id: str | None = None
    content_type: str = "text"
    thread_id: int | None = None  # Telegram topic thread_id for targeted send
    retry_count: int = 0  # Number of times this task has been retried


def get_message_queue(
    agent_ctx: AgentContext, user_id: int
) -> asyncio.Queue[MessageTask] | None:
    """Get the message queue for a user (if exists)."""
    return agent_ctx.queue_state.queues.get(user_id)


def get_or_create_queue(
    bot: Bot,
    user_id: int,
    agent_ctx: AgentContext,
) -> asyncio.Queue[MessageTask]:
    """Get or create message queue and worker for a user."""
    qs = agent_ctx.queue_state
    if user_id not in qs.queues:
        qs.queues[user_id] = asyncio.Queue()
        qs.locks[user_id] = asyncio.Lock()
        # Start worker task for this user
        qs.workers[user_id] = asyncio.create_task(
            _message_queue_worker(bot, user_id, agent_ctx)
        )
    return qs.queues[user_id]


def _inspect_queue(queue: asyncio.Queue[MessageTask]) -> list[MessageTask]:
    """Non-destructively inspect all items in queue.

    Drains the queue and returns all items. Caller must refill.
    """
    items: list[MessageTask] = []
    while not queue.empty():
        try:
            item = queue.get_nowait()
            items.append(item)
        except asyncio.QueueEmpty:
            break
    return items


def _can_merge_tasks(base: MessageTask, candidate: MessageTask) -> bool:
    """Check if two content tasks can be merged."""
    if base.window_id != candidate.window_id:
        return False
    if candidate.task_type != "content":
        return False
    # tool_use/tool_result break merge chain
    # - tool_use: will be edited later by tool_result
    # - tool_result: edits previous message, merging would cause order issues
    if base.content_type in ("tool_use", "tool_result"):
        return False
    if candidate.content_type in ("tool_use", "tool_result"):
        return False
    return True


async def _merge_content_tasks(
    queue: asyncio.Queue[MessageTask],
    first: MessageTask,
    lock: asyncio.Lock,
) -> tuple[MessageTask, int]:
    """Merge consecutive content tasks from queue.

    Returns: (merged_task, merge_count) where merge_count is the number of
    additional tasks merged (0 if no merging occurred).

    Note on queue counter management:
        When we put items back, we call task_done() to compensate for the
        internal counter increment caused by put_nowait(). This is necessary
        because the items were already counted when originally enqueued.
        Without this compensation, queue.join() would wait indefinitely.
    """
    merged_parts = list(first.parts)
    current_length = sum(len(p) for p in merged_parts)
    merge_count = 0

    async with lock:
        items = _inspect_queue(queue)
        remaining: list[MessageTask] = []

        for i, task in enumerate(items):
            if not _can_merge_tasks(first, task):
                # Can't merge, keep this and all remaining items
                remaining = items[i:]
                break

            # Check length before merging
            task_length = sum(len(p) for p in task.parts)
            if current_length + task_length > MERGE_MAX_LENGTH:
                # Too long, stop merging
                remaining = items[i:]
                break

            merged_parts.extend(task.parts)
            current_length += task_length
            merge_count += 1

        # Put remaining items back into the queue
        for item in remaining:
            queue.put_nowait(item)
            # Compensate: this item was already counted when first enqueued,
            # put_nowait adds a duplicate count that must be removed
            queue.task_done()

    if merge_count == 0:
        return first, 0

    return (
        MessageTask(
            task_type="content",
            window_id=first.window_id,
            parts=merged_parts,
            tool_use_id=first.tool_use_id,
            content_type=first.content_type,
            thread_id=first.thread_id,
        ),
        merge_count,
    )


async def _message_queue_worker(
    bot: Bot, user_id: int, agent_ctx: AgentContext
) -> None:
    """Process message tasks for a user sequentially."""
    qs = agent_ctx.queue_state
    queue = qs.queues[user_id]
    lock = qs.locks[user_id]
    logger.info(f"Message queue worker started for user {user_id}")

    while True:
        try:
            task = await queue.get()
            try:
                if task.task_type == "content":
                    # Try to merge consecutive content tasks
                    merged_task, merge_count = await _merge_content_tasks(
                        queue, task, lock
                    )
                    if merge_count > 0:
                        logger.debug(f"Merged {merge_count} tasks for user {user_id}")
                        # Mark merged tasks as done
                        for _ in range(merge_count):
                            queue.task_done()
                    await _process_content_task(bot, user_id, merged_task, agent_ctx)
                elif task.task_type == "status_update":
                    await _process_status_update_task(bot, user_id, task, agent_ctx)
                elif task.task_type == "status_clear":
                    await _do_clear_status_message(
                        bot, user_id, task.thread_id or 0, agent_ctx
                    )
            except RetryAfter as e:
                retry_secs = (
                    e.retry_after
                    if isinstance(e.retry_after, int)
                    else int(e.retry_after.total_seconds())
                )
                logger.warning(
                    f"Flood control for user {user_id}, pausing {retry_secs}s"
                )
                # Log periodically during long waits
                remaining = retry_secs
                while remaining > 0:
                    chunk = min(remaining, 30)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                    if remaining > 0:
                        logger.warning(
                            f"Flood control for user {user_id}, {remaining}s remaining"
                        )
            except (TimedOut, NetworkError) as e:
                if task.retry_count < _WORKER_MAX_RETRIES:
                    task.retry_count += 1
                    logger.warning(
                        "Network error for user %d (retry %d/%d): %s, "
                        "re-queuing in %ds",
                        user_id,
                        task.retry_count,
                        _WORKER_MAX_RETRIES,
                        e,
                        _WORKER_RETRY_DELAY,
                    )
                    await asyncio.sleep(_WORKER_RETRY_DELAY)
                    # Re-insert at front of queue by draining and re-filling
                    async with lock:
                        remaining_items = _inspect_queue(queue)
                        queue.put_nowait(task)
                        queue.task_done()  # compensate for put_nowait counter
                        for item in remaining_items:
                            queue.put_nowait(item)
                            queue.task_done()  # compensate
                else:
                    logger.error(
                        "Network error for user %d after %d retries, dropping task: %s",
                        user_id,
                        _WORKER_MAX_RETRIES,
                        e,
                    )
            except Exception as e:
                logger.error(f"Error processing message task for user {user_id}: {e}")
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"Message queue worker cancelled for user {user_id}")
            break
        except Exception as e:
            logger.error(f"Unexpected error in queue worker for user {user_id}: {e}")


def _send_kwargs(thread_id: int | None) -> dict[str, int]:
    """Build message_thread_id kwargs for bot.send_message()."""
    if thread_id is not None:
        return {"message_thread_id": thread_id}
    return {}


async def _process_content_task(
    bot: Bot, user_id: int, task: MessageTask, agent_ctx: AgentContext
) -> None:
    """Process a content message task."""
    sm = agent_ctx.session_manager
    qs = agent_ctx.queue_state
    wid = task.window_id or ""
    tid = task.thread_id or 0
    chat_id = sm.resolve_chat_id(user_id, task.thread_id)

    # 1. Handle tool_result editing (merged parts are edited together)
    if task.content_type == "tool_result" and task.tool_use_id:
        _tkey = (task.tool_use_id, user_id, tid)
        edit_msg_id = qs.tool_msg_ids.pop(_tkey, None)
        if edit_msg_id is not None:
            # Clear status message first
            await _do_clear_status_message(bot, user_id, tid, agent_ctx)
            # Join all parts for editing (merged content goes together)
            full_text = "\n\n".join(task.parts)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=edit_msg_id,
                    text=convert_markdown(full_text),
                    parse_mode="MarkdownV2",
                    link_preview_options=NO_LINK_PREVIEW,
                )
                await _check_and_send_status(
                    bot, user_id, wid, task.thread_id, agent_ctx
                )
                return
            except RetryAfter:
                raise
            except Exception:
                try:
                    # Fallback: send as plain text
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=edit_msg_id,
                        text=full_text,
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                    await _check_and_send_status(
                        bot, user_id, wid, task.thread_id, agent_ctx
                    )
                    return
                except RetryAfter:
                    raise
                except Exception:
                    logger.debug(f"Failed to edit tool msg {edit_msg_id}, sending new")
                    # Fall through to send as new message

    # 2. Send content messages, converting status message to first content part
    first_part = True
    last_msg_id: int | None = None
    for part in task.parts:
        sent = None

        # For first part, try to convert status message to content (edit instead of delete)
        if first_part:
            first_part = False
            converted_msg_id = await _convert_status_to_content(
                bot,
                user_id,
                tid,
                wid,
                part,
                agent_ctx,
            )
            if converted_msg_id is not None:
                last_msg_id = converted_msg_id
                continue

        sent = await rate_limit_send_message(
            bot,
            chat_id,
            part,
            **_send_kwargs(task.thread_id),  # type: ignore[arg-type]
        )

        if sent:
            last_msg_id = sent.message_id

    # 3. Record tool_use message ID for later editing
    if last_msg_id and task.tool_use_id and task.content_type == "tool_use":
        qs.tool_msg_ids[(task.tool_use_id, user_id, tid)] = last_msg_id

    # 4. After content, check and send status
    await _check_and_send_status(bot, user_id, wid, task.thread_id, agent_ctx)


async def _convert_status_to_content(
    bot: Bot,
    user_id: int,
    thread_id_or_0: int,
    window_id: str,
    content_text: str,
    agent_ctx: AgentContext,
) -> int | None:
    """Convert status message to content message by editing it.

    Returns the message_id if converted successfully, None otherwise.
    """
    sm = agent_ctx.session_manager
    qs = agent_ctx.queue_state
    skey = (user_id, thread_id_or_0)
    info = qs.status_msg_info.pop(skey, None)
    if not info:
        return None

    thread_id: int | None = thread_id_or_0 if thread_id_or_0 != 0 else None
    chat_id = sm.resolve_chat_id(user_id, thread_id)

    msg_id, stored_wid, _last_text = info
    if stored_wid != window_id:
        # Different window, just delete the old status
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
        return None

    # Edit status message to show content
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=convert_markdown(content_text),
            parse_mode="MarkdownV2",
            link_preview_options=NO_LINK_PREVIEW,
        )
        return msg_id
    except RetryAfter:
        raise
    except Exception:
        try:
            # Fallback to plain text
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=content_text,
                link_preview_options=NO_LINK_PREVIEW,
            )
            return msg_id
        except RetryAfter:
            raise
        except Exception as e:
            logger.debug(f"Failed to convert status to content: {e}")
            # Message might be deleted or too old, caller will send new message
            return None


async def _process_status_update_task(
    bot: Bot, user_id: int, task: MessageTask, agent_ctx: AgentContext
) -> None:
    """Process a status update task."""
    sm = agent_ctx.session_manager
    qs = agent_ctx.queue_state
    wid = task.window_id or ""
    tid = task.thread_id or 0
    chat_id = sm.resolve_chat_id(user_id, task.thread_id)
    skey = (user_id, tid)
    status_text = task.text or ""

    if not status_text:
        # No status text means clear status
        await _do_clear_status_message(bot, user_id, tid, agent_ctx)
        return

    # Send typing indicator if Claude is interruptible (working)
    from telegram.constants import ChatAction

    if "esc to interrupt" in status_text.lower():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass

    current_info = qs.status_msg_info.get(skey)

    if current_info:
        msg_id, stored_wid, last_text = current_info

        if stored_wid != wid:
            # Window changed - delete old and send new
            await _do_clear_status_message(bot, user_id, tid, agent_ctx)
            await _do_send_status_message(
                bot, user_id, tid, wid, status_text, agent_ctx
            )
        elif status_text == last_text:
            # Same content, skip edit
            pass
        else:
            # Same window, text changed - edit in place
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=convert_markdown(status_text),
                    parse_mode="MarkdownV2",
                    link_preview_options=NO_LINK_PREVIEW,
                )
                qs.status_msg_info[skey] = (msg_id, wid, status_text)
            except RetryAfter:
                raise
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=status_text,
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                    qs.status_msg_info[skey] = (msg_id, wid, status_text)
                except RetryAfter:
                    raise
                except Exception as e:
                    logger.debug(f"Failed to edit status message: {e}")
                    qs.status_msg_info.pop(skey, None)
                    await _do_send_status_message(
                        bot, user_id, tid, wid, status_text, agent_ctx
                    )
    else:
        # No existing status message, send new
        await _do_send_status_message(bot, user_id, tid, wid, status_text, agent_ctx)


async def _do_send_status_message(
    bot: Bot,
    user_id: int,
    thread_id_or_0: int,
    window_id: str,
    text: str,
    agent_ctx: AgentContext,
) -> None:
    """Send a new status message and track it (internal, called from worker)."""
    sm = agent_ctx.session_manager
    qs = agent_ctx.queue_state
    skey = (user_id, thread_id_or_0)
    thread_id: int | None = thread_id_or_0 if thread_id_or_0 != 0 else None
    chat_id = sm.resolve_chat_id(user_id, thread_id)
    sent = await rate_limit_send_message(
        bot,
        chat_id,
        text,
        **_send_kwargs(thread_id),  # type: ignore[arg-type]
    )
    if sent:
        qs.status_msg_info[skey] = (sent.message_id, window_id, text)


async def _do_clear_status_message(
    bot: Bot,
    user_id: int,
    thread_id_or_0: int,
    agent_ctx: AgentContext,
) -> None:
    """Delete the status message for a user (internal, called from worker)."""
    sm = agent_ctx.session_manager
    qs = agent_ctx.queue_state
    skey = (user_id, thread_id_or_0)
    info = qs.status_msg_info.pop(skey, None)
    if info:
        msg_id = info[0]
        thread_id: int | None = thread_id_or_0 if thread_id_or_0 != 0 else None
        chat_id = sm.resolve_chat_id(user_id, thread_id)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.debug(f"Failed to delete status message {msg_id}: {e}")


async def _check_and_send_status(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None,
    agent_ctx: AgentContext,
) -> None:
    """Check terminal for status line and send status message if present."""
    tm = agent_ctx.tmux_manager
    qs = agent_ctx.queue_state
    # Skip if there are more messages pending in the queue
    queue = qs.queues.get(user_id)
    if queue and not queue.empty():
        return
    w = await tm.find_window_by_id(window_id)
    if not w:
        return

    pane_text = await tm.capture_pane(w.window_id)
    if not pane_text:
        return

    tid = thread_id or 0
    status_line = parse_status_line(pane_text)
    if status_line:
        await _do_send_status_message(
            bot, user_id, tid, window_id, status_line, agent_ctx
        )


async def enqueue_content_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    parts: list[str],
    tool_use_id: str | None = None,
    content_type: str = "text",
    text: str | None = None,
    thread_id: int | None = None,
    *,
    agent_ctx: AgentContext,
) -> None:
    """Enqueue a content message task."""
    logger.debug(
        "Enqueue content: user=%d, window_id=%s, content_type=%s",
        user_id,
        window_id,
        content_type,
    )
    queue = get_or_create_queue(bot, user_id, agent_ctx)

    task = MessageTask(
        task_type="content",
        text=text,
        window_id=window_id,
        parts=parts,
        tool_use_id=tool_use_id,
        content_type=content_type,
        thread_id=thread_id,
    )
    queue.put_nowait(task)


async def enqueue_status_update(
    bot: Bot,
    user_id: int,
    window_id: str,
    status_text: str | None,
    thread_id: int | None = None,
    *,
    agent_ctx: AgentContext,
) -> None:
    """Enqueue status update."""
    queue = get_or_create_queue(bot, user_id, agent_ctx)

    if status_text:
        task = MessageTask(
            task_type="status_update",
            text=status_text,
            window_id=window_id,
            thread_id=thread_id,
        )
    else:
        task = MessageTask(task_type="status_clear", thread_id=thread_id)

    queue.put_nowait(task)


def clear_status_msg_info(
    agent_ctx: AgentContext,
    user_id: int,
    thread_id: int | None = None,
) -> None:
    """Clear status message tracking for a user (and optionally a specific thread)."""
    skey = (user_id, thread_id or 0)
    agent_ctx.queue_state.status_msg_info.pop(skey, None)


def clear_tool_msg_ids_for_topic(
    agent_ctx: AgentContext,
    user_id: int,
    thread_id: int | None = None,
) -> None:
    """Clear tool message ID tracking for a specific topic.

    Removes all entries in tool_msg_ids that match the given user and thread.
    """
    tid = thread_id or 0
    tool_msg_ids = agent_ctx.queue_state.tool_msg_ids
    # Find and remove all matching keys
    keys_to_remove = [
        key for key in tool_msg_ids if key[1] == user_id and key[2] == tid
    ]
    for key in keys_to_remove:
        tool_msg_ids.pop(key, None)


async def shutdown_workers(agent_ctx: AgentContext) -> None:
    """Stop all queue workers for this agent (called during bot shutdown)."""
    qs = agent_ctx.queue_state
    for user_id, worker in list(qs.workers.items()):
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
    qs.workers.clear()
    qs.queues.clear()
    qs.locks.clear()
    logger.info("Message queue workers stopped")
