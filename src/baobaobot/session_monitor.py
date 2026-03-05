"""Session monitoring service — watches transcript files for new messages.

Runs an async polling loop that:
  1. Loads the current session_map to know which sessions to watch.
  2. Detects session_map changes (new/changed/deleted windows) and cleans up.
  3. Reads new content from each session file:
     - JSONL (Claude): incremental line reading with byte-offset tracking.
     - Full JSON (Gemini): reads entire file, tracks message count.
  4. Parses entries via backend-specific parsers and emits NewMessage objects.

Optimizations: mtime cache skips unchanged files; offset avoids re-reading.

Key classes: SessionMonitor, NewMessage, SessionInfo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Awaitable

import aiofiles

from .monitor_state import MonitorState, TrackedSession
from .transcript_parser import TranscriptParser
from .utils import read_cwd_from_jsonl

if TYPE_CHECKING:
    from .backends.base import TmuxCliBackend  # noqa: F401
    from .session import SessionManager
    from .tmux_manager import TmuxManager

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Information about a Claude Code session."""

    session_id: str
    file_path: Path


_SEND_FILE_RE = re.compile(r"\[SEND_FILE:([^\]]+)\]")
_SHARE_LINK_RE = re.compile(r"\[SHARE_LINK:([^\]]+)\]")
_UPLOAD_LINK_RE = re.compile(r"\[UPLOAD_LINK(?::([^\]]*))?\]")
_CODE_LINK_RE = re.compile(r"\[CODE_LINK:([^\]]+)\]")
_SENDABLE_EXTENSIONS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    # Web
    ".html",
    # Documents
    ".pdf",
    ".docx",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".doc",
    ".csv",
    ".txt",
    ".rtf",
    # Archives
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
}
_READ_PATH_RE = re.compile(r"\*\*Read\*\*\((.+)\)")


@dataclass
class NewMessage:
    """A new message detected by the monitor."""

    session_id: str
    text: str
    is_complete: bool  # True when stop_reason is set (final message)
    content_type: str = "text"  # "text" or "thinking"
    tool_use_id: str | None = None
    role: str = "assistant"  # "user" or "assistant"
    tool_name: str | None = None  # For tool_use messages, the tool name
    file_paths: list[str] = field(default_factory=list)  # [SEND_FILE:path] matches
    share_links: list[str] = field(default_factory=list)  # [SHARE_LINK:path] matches
    upload_links: list[str] = field(default_factory=list)  # [UPLOAD_LINK] matches
    code_links: list[str] = field(default_factory=list)  # [CODE_LINK:path] matches


class SessionMonitor:
    """Monitors Claude Code sessions for new assistant messages.

    Uses simple async polling with aiofiles for non-blocking I/O.
    Emits both intermediate and complete assistant messages.
    """

    def __init__(
        self,
        *,
        tmux_manager: TmuxManager,
        session_manager: SessionManager,
        session_map_file: Path,
        tmux_session_name: str,
        poll_interval: float,
        state_file: Path,
        agent_name: str = "",
        backend: TmuxCliBackend | None = None,
        get_window_backend: Callable[[str], TmuxCliBackend | None] | None = None,
    ):
        self._tmux_manager = tmux_manager
        self._session_manager = session_manager
        self._session_map_file = session_map_file
        self._tmux_session_name = tmux_session_name
        self.projects_path = (
            backend.projects_path
            if backend is not None
            else Path.home() / ".claude" / "projects"
        )
        self.poll_interval = poll_interval
        self._agent_name = agent_name
        self._backend = backend
        self._get_window_backend = get_window_backend

        self.state = MonitorState(state_file=state_file)
        self.state.load()

        self._running = False
        self._task: asyncio.Task | None = None
        self._message_callback: Callable[[NewMessage], Awaitable[None]] | None = None
        # Per-session pending tool_use state carried across poll cycles
        self._pending_tools: dict[str, dict[str, Any]] = {}  # session_id -> pending
        # Per-session [NO_NOTIFY] active state carried across poll cycles
        self._no_notify_active: dict[str, bool] = {}  # session_id -> active
        # Track last known session_map for detecting changes
        # Keys may be window_id (@12) or window_name (old format) during transition
        self._last_session_map: dict[str, str] = {}  # window_key -> session_id
        # In-memory mtime cache for quick file change detection (not persisted)
        self._file_mtimes: dict[str, float] = {}  # session_id -> last_seen_mtime

    def set_message_callback(
        self, callback: Callable[[NewMessage], Awaitable[None]]
    ) -> None:
        self._message_callback = callback

    async def _get_active_cwds(self) -> set[str]:
        """Get normalized cwds of all active tmux windows for this agent."""
        cwds = set()
        agent_prefix = f"{self._agent_name}/" if self._agent_name else ""
        windows = await self._tmux_manager.list_windows()
        for w in windows:
            # Only consider windows belonging to this agent
            if agent_prefix and not w.window_name.startswith(agent_prefix):
                continue
            try:
                cwds.add(str(Path(w.cwd).resolve()))
            except (OSError, ValueError):
                cwds.add(w.cwd)
        return cwds

    def _collect_active_backends(self) -> list[TmuxCliBackend]:
        """Collect all unique active backends across windows.

        When per-window backends are configured, multiple backend types may
        be active simultaneously (e.g. Claude + Gemini windows).
        """
        from .backends.base import TmuxCliBackend as _TmuxCliBackend

        seen_types: set[str] = set()
        backends: list[TmuxCliBackend] = []

        # Always include the default backend
        if self._backend is not None:
            seen_types.add(self._backend.agent_type)
            backends.append(self._backend)

        # Add per-window backends if resolver is available
        if self._get_window_backend is not None:
            for wid, state in self._session_manager.window_states.items():
                if state.agent_type and state.agent_type not in seen_types:
                    wb = self._get_window_backend(wid)
                    if wb is not None and isinstance(wb, _TmuxCliBackend):
                        seen_types.add(state.agent_type)
                        backends.append(wb)

        return backends

    async def scan_projects(self) -> list[SessionInfo]:
        """Scan projects that have active tmux windows.

        When per-window backends are configured, scans all active backend
        types to discover session files from multiple CLI tools.
        """
        active_cwds = await self._get_active_cwds()
        if not active_cwds:
            return []

        # Collect all active backends and scan each
        backends = self._collect_active_backends()
        if backends:
            all_sessions: list[SessionInfo] = []
            seen_ids: set[str] = set()
            for be in backends:
                backend_results = await be.scan_session_files(active_cwds)
                for r in backend_results:
                    if r.session_id not in seen_ids:
                        seen_ids.add(r.session_id)
                        all_sessions.append(
                            SessionInfo(session_id=r.session_id, file_path=r.file_path)
                        )
            return all_sessions

        # Fallback: original Claude-specific scanning (for backward compat)
        sessions: list[SessionInfo] = []

        if not self.projects_path.exists():
            return sessions

        for project_dir in self.projects_path.iterdir():
            if not project_dir.is_dir():
                continue

            index_file = project_dir / "sessions-index.json"
            original_path = ""
            indexed_ids: set[str] = set()

            if index_file.exists():
                try:
                    async with aiofiles.open(index_file, "r") as f:
                        content = await f.read()
                    index_data = json.loads(content)
                    entries = index_data.get("entries", [])
                    original_path = index_data.get("originalPath", "")

                    for entry in entries:
                        session_id = entry.get("sessionId", "")
                        full_path = entry.get("fullPath", "")
                        project_path = entry.get("projectPath", original_path)

                        if not session_id or not full_path:
                            continue

                        try:
                            norm_pp = str(Path(project_path).resolve())
                        except (OSError, ValueError):
                            norm_pp = project_path
                        if norm_pp not in active_cwds:
                            continue

                        indexed_ids.add(session_id)
                        file_path = Path(full_path)
                        if file_path.exists():
                            sessions.append(
                                SessionInfo(
                                    session_id=session_id,
                                    file_path=file_path,
                                )
                            )

                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"Error reading index {index_file}: {e}")

            # Pick up un-indexed .jsonl files
            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    session_id = jsonl_file.stem
                    if session_id in indexed_ids:
                        continue

                    # Determine project_path for this file
                    file_project_path = original_path
                    if not file_project_path:
                        file_project_path = await asyncio.to_thread(
                            read_cwd_from_jsonl, jsonl_file
                        )
                    if not file_project_path:
                        dir_name = project_dir.name
                        if dir_name.startswith("-"):
                            file_project_path = dir_name.replace("-", "/")

                    try:
                        norm_fp = str(Path(file_project_path).resolve())
                    except (OSError, ValueError):
                        norm_fp = file_project_path

                    if norm_fp not in active_cwds:
                        continue

                    sessions.append(
                        SessionInfo(
                            session_id=session_id,
                            file_path=jsonl_file,
                        )
                    )
            except OSError as e:
                logger.debug(f"Error scanning jsonl files in {project_dir}: {e}")

        return sessions

    async def _read_new_lines(
        self,
        session: TrackedSession,
        file_path: Path,
        backend: TmuxCliBackend | None = None,
    ) -> list[dict]:
        """Read new lines from a session file using byte offset for efficiency.

        Detects file truncation (e.g. after /clear) and resets offset.

        Args:
            session: The tracked session state.
            file_path: Path to the transcript file.
            backend: Backend to use for line parsing (defaults to self._backend).
        """
        effective_backend = backend if backend is not None else self._backend
        new_entries = []
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                # Get file size to detect truncation
                await f.seek(0, 2)  # Seek to end
                file_size = await f.tell()

                # Detect file truncation: if offset is beyond file size, reset
                if session.last_byte_offset > file_size:
                    logger.info(
                        "File truncated for session %s "
                        "(offset %d > size %d). Resetting.",
                        session.session_id,
                        session.last_byte_offset,
                        file_size,
                    )
                    session.last_byte_offset = 0

                # Seek to last read position for incremental reading
                await f.seek(session.last_byte_offset)

                # Read only new lines from the offset.
                # Track safe_offset: only advance past lines that parsed
                # successfully. A non-empty line that fails JSON parsing is
                # likely a partial write; stop and retry next cycle.
                parse_line = (
                    effective_backend.parse_transcript_line
                    if effective_backend is not None
                    else TranscriptParser.parse_line
                )
                safe_offset = session.last_byte_offset
                async for line in f:
                    data = parse_line(line)
                    if data:
                        new_entries.append(data)
                        safe_offset = await f.tell()
                    elif line.strip():
                        # Partial JSONL line — don't advance offset past it
                        logger.warning(
                            "Partial JSONL line in session %s, will retry next cycle",
                            session.session_id,
                        )
                        break
                    else:
                        # Empty line — safe to skip
                        safe_offset = await f.tell()

                session.last_byte_offset = safe_offset

        except OSError as e:
            logger.error("Error reading session file %s: %s", file_path, e)
        return new_entries

    @staticmethod
    def _msg_hash(msg: dict) -> str:
        """Hash a message dict for in-place update detection."""
        return hashlib.md5(
            json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    async def _read_full_json(
        self, session: TrackedSession, file_path: Path
    ) -> list[dict]:
        """Read new messages from a full-JSON session file (e.g. Gemini).

        Unlike JSONL, the entire file is a single JSON object with a
        ``messages`` array.  We track the number of messages already
        processed via ``last_byte_offset`` (repurposed as message count).

        Also detects in-place updates to the last-seen message (e.g.,
        Gemini adding ``toolCalls`` after the initial content write).
        Updated messages are returned with a ``_recheck`` flag so the
        parser can emit only the new entries (tool_use/tool_result).
        """
        new_entries: list[dict] = []
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            data = json.loads(content)
            messages = data.get("messages", [])
            already_seen = session.last_byte_offset  # repurposed as msg count

            if len(messages) <= already_seen:
                # No new messages — check for in-place updates on the last one
                if already_seen > 0 and messages:
                    last_msg = messages[already_seen - 1]
                    new_hash = self._msg_hash(last_msg)
                    old_hash = getattr(session, "_last_msg_hash", None)
                    if old_hash is not None and new_hash != old_hash:
                        logger.debug(
                            "Detected in-place update on msg[%d] (hash %s→%s)",
                            already_seen - 1,
                            old_hash[:8],
                            new_hash[:8],
                        )
                        session._last_msg_hash = new_hash  # type: ignore[attr-defined]
                        new_entries.append({"_recheck": True, **last_msg})
                return new_entries

            # Extract only the new messages
            new_messages = messages[already_seen:]
            session.last_byte_offset = len(messages)

            # Store hash of the last message for future in-place update detection
            if messages:
                session._last_msg_hash = self._msg_hash(messages[-1])  # type: ignore[attr-defined]

            # Return raw message dicts for parse_entries to handle
            new_entries.extend(new_messages)

        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error reading full-JSON session file %s: %s", file_path, e)
        return new_entries

    def _resolve_backend_for_session(
        self,
        session_id: str,
    ) -> TmuxCliBackend | None:
        """Find the backend for a session_id by tracing session_map → window → backend."""
        if self._get_window_backend is not None:
            for window_key, sid in self._last_session_map.items():
                if sid == session_id:
                    wb = self._get_window_backend(window_key)
                    if wb is not None:
                        return wb
        return self._backend

    async def check_for_updates(self, active_session_ids: set[str]) -> list[NewMessage]:
        """Check all sessions for new assistant messages.

        Reads from last byte offset. Emits both intermediate
        (stop_reason=null) and complete messages.

        Args:
            active_session_ids: Set of session IDs currently in session_map
        """
        new_messages = []

        # Scan projects to get available session files
        sessions = await self.scan_projects()

        # Fallback for Gemini: if a session_id from session_map has no
        # matching file (race condition at startup), find the latest file
        # for the window's cwd and add it to the sessions list.
        found_sids = {s.session_id for s in sessions}
        for missing_sid in active_session_ids - found_sids:
            backend = self._resolve_backend_for_session(missing_sid)
            if backend is None or not getattr(backend, "is_full_json", False):
                continue
            if not hasattr(backend, "find_latest_session_file"):
                continue
            # Look up the cwd from window_states
            cwd = ""
            for wkey, sid in self._last_session_map.items():
                if sid == missing_sid:
                    ws = self._session_manager.window_states.get(wkey)
                    if ws:
                        cwd = ws.cwd
                    break
            if not cwd:
                continue
            result = backend.find_latest_session_file(cwd)
            if result is not None:
                file_sid, file_path = result
                if file_sid != missing_sid:
                    # Only log once per session (avoid spamming every poll)
                    fallback_key = f"_fallback_logged_{missing_sid}"
                    if not getattr(self, fallback_key, False):
                        logger.info(
                            "Gemini fallback: session %s not found, using "
                            "latest file %s (session_id=%s)",
                            missing_sid[:8],
                            file_path.name,
                            file_sid[:8],
                        )
                        setattr(self, fallback_key, True)
                # Add with the ORIGINAL session_id so routing works
                sessions.append(
                    SessionInfo(session_id=missing_sid, file_path=file_path)
                )

        # Only process sessions that are in session_map
        for session_info in sessions:
            if session_info.session_id not in active_session_ids:
                continue
            try:
                # Resolve the correct backend for this session
                session_backend = self._resolve_backend_for_session(
                    session_info.session_id
                )
                tracked = self.state.get_session(session_info.session_id)

                # Detect file path change (e.g., Gemini fallback resolved
                # to a newer file). Reset offset to avoid stale data.
                if (
                    tracked is not None
                    and tracked.file_path
                    and str(session_info.file_path) != tracked.file_path
                ):
                    logger.info(
                        "Session %s file changed: %s -> %s, resetting offset",
                        session_info.session_id[:8],
                        Path(tracked.file_path).name,
                        session_info.file_path.name,
                    )
                    tracked.file_path = str(session_info.file_path)
                    tracked.last_byte_offset = 0
                    self._file_mtimes.pop(session_info.session_id, None)

                if tracked is None:
                    # For new sessions, initialize offset to end to avoid
                    # re-processing old messages.
                    # For full-JSON backends: offset = message count
                    # For JSONL backends: offset = file size in bytes
                    is_full_json = (
                        getattr(session_backend, "is_full_json", False)
                        if session_backend is not None
                        else False
                    )
                    try:
                        current_mtime = session_info.file_path.stat().st_mtime
                        if is_full_json:
                            data = json.loads(session_info.file_path.read_text())
                            initial_offset = len(data.get("messages", []))
                        else:
                            initial_offset = session_info.file_path.stat().st_size
                    except (OSError, json.JSONDecodeError):
                        initial_offset = 0
                        current_mtime = 0.0
                    tracked = TrackedSession(
                        session_id=session_info.session_id,
                        file_path=str(session_info.file_path),
                        last_byte_offset=initial_offset,
                    )
                    self.state.update_session(tracked)
                    self._file_mtimes[session_info.session_id] = current_mtime
                    logger.info(f"Started tracking session: {session_info.session_id}")
                    continue

                # Check mtime to see if file has changed
                try:
                    current_mtime = session_info.file_path.stat().st_mtime
                except OSError:
                    continue

                last_mtime = self._file_mtimes.get(session_info.session_id, 0.0)
                if current_mtime <= last_mtime:
                    # File hasn't changed, skip reading
                    continue

                # File changed, read new content
                is_full_json = (
                    getattr(session_backend, "is_full_json", False)
                    if session_backend is not None
                    else False
                )
                if is_full_json:
                    new_entries = await self._read_full_json(
                        tracked, session_info.file_path
                    )
                else:
                    new_entries = await self._read_new_lines(
                        tracked, session_info.file_path, backend=session_backend
                    )
                self._file_mtimes[session_info.session_id] = current_mtime

                if new_entries:
                    logger.debug(
                        f"Read {len(new_entries)} new entries for "
                        f"session {session_info.session_id}"
                    )

                # Parse new entries using the shared logic, carrying over pending tools
                carry = self._pending_tools.get(session_info.session_id, {})
                nn_active = self._no_notify_active.get(session_info.session_id, False)
                parse_entries_fn = (
                    session_backend.parse_transcript_entries
                    if session_backend is not None
                    else TranscriptParser.parse_entries
                )
                parsed_entries, remaining, nn_active = parse_entries_fn(
                    new_entries,
                    pending_tools=carry,
                    no_notify_active=nn_active,
                )
                if remaining:
                    self._pending_tools[session_info.session_id] = remaining
                else:
                    self._pending_tools.pop(session_info.session_id, None)
                if nn_active:
                    self._no_notify_active[session_info.session_id] = True
                else:
                    self._no_notify_active.pop(session_info.session_id, None)

                for entry in parsed_entries:
                    if not entry.text:
                        continue
                    # Skip [NO_NOTIFY] tagged messages
                    if entry.no_notify:
                        continue
                    # Extract markers from assistant text
                    file_paths: list[str] = []
                    share_links: list[str] = []
                    upload_links: list[str] = []
                    code_links: list[str] = []
                    if entry.role == "assistant" and entry.content_type == "text":
                        file_paths = _SEND_FILE_RE.findall(entry.text)
                        share_links = _SHARE_LINK_RE.findall(entry.text)
                        upload_links = _UPLOAD_LINK_RE.findall(entry.text)
                        code_links = _CODE_LINK_RE.findall(entry.text)
                    # Auto-send images when Claude Code reads them via Read tool
                    if entry.content_type == "tool_use" and entry.tool_name == "Read":
                        m = _READ_PATH_RE.match(entry.text)
                        if m:
                            p = Path(m.group(1))
                            if p.suffix.lower() in _SENDABLE_EXTENSIONS:
                                file_paths.append(str(p))
                    new_messages.append(
                        NewMessage(
                            session_id=session_info.session_id,
                            text=entry.text,
                            is_complete=True,
                            content_type=entry.content_type,
                            tool_use_id=entry.tool_use_id,
                            role=entry.role,
                            tool_name=entry.tool_name,
                            file_paths=file_paths,
                            share_links=share_links,
                            upload_links=upload_links,
                            code_links=code_links,
                        )
                    )

                self.state.update_session(tracked)

            except OSError as e:
                logger.debug(f"Error processing session {session_info.session_id}: {e}")

        self.state.save_if_dirty()
        return new_messages

    async def _load_current_session_map(self) -> dict[str, str]:
        """Load current session_map and return window_key -> session_id mapping.

        Keys in session_map are formatted as "tmux_session:window_id"
        (e.g. "baobaobot:@12"). Old-format keys ("baobaobot:window_name") are also
        accepted so that sessions running before a code upgrade continue
        to be monitored until the hook re-fires with new format.
        Only entries matching our tmux_session_name are processed.
        """
        window_to_session: dict[str, str] = {}
        if self._session_map_file.exists():
            try:
                async with aiofiles.open(self._session_map_file, "r") as f:
                    content = await f.read()
                session_map = json.loads(content)
                prefix = f"{self._tmux_session_name}:"
                for key, info in session_map.items():
                    # Only process entries for our tmux session
                    if not key.startswith(prefix):
                        continue
                    window_key = key[len(prefix) :]
                    session_id = info.get("session_id", "")
                    if session_id:
                        window_to_session[window_key] = session_id
            except (json.JSONDecodeError, OSError):
                pass
        return window_to_session

    async def _cleanup_all_stale_sessions(self) -> None:
        """Clean up all tracked sessions not in current session_map (used on startup)."""
        current_map = await self._load_current_session_map()
        active_session_ids = set(current_map.values())

        stale_sessions = []
        for session_id in self.state.tracked_sessions.keys():
            if session_id not in active_session_ids:
                stale_sessions.append(session_id)

        if stale_sessions:
            logger.info(
                f"[Startup cleanup] Removing {len(stale_sessions)} stale sessions"
            )
            for session_id in stale_sessions:
                self.state.remove_session(session_id)
                self._file_mtimes.pop(session_id, None)
            self.state.save_if_dirty()

    async def _detect_and_cleanup_changes(self) -> dict[str, str]:
        """Detect session_map changes and cleanup replaced/removed sessions.

        Returns current session_map for further processing.
        """
        current_map = await self._load_current_session_map()

        sessions_to_remove: set[str] = set()

        # Check for window session changes (window exists in both, but session_id changed)
        for window_id, old_session_id in self._last_session_map.items():
            new_session_id = current_map.get(window_id)
            if new_session_id and new_session_id != old_session_id:
                logger.info(
                    "Window '%s' session changed: %s -> %s",
                    window_id,
                    old_session_id,
                    new_session_id,
                )
                sessions_to_remove.add(old_session_id)

        # Check for deleted windows (window in old map but not in current)
        old_windows = set(self._last_session_map.keys())
        current_windows = set(current_map.keys())
        deleted_windows = old_windows - current_windows

        for window_id in deleted_windows:
            old_session_id = self._last_session_map[window_id]
            logger.info(
                "Window '%s' deleted, removing session %s",
                window_id,
                old_session_id,
            )
            sessions_to_remove.add(old_session_id)

        # Perform cleanup
        if sessions_to_remove:
            for session_id in sessions_to_remove:
                self.state.remove_session(session_id)
                self._file_mtimes.pop(session_id, None)
                # Clear fallback log flag
                fallback_key = f"_fallback_logged_{session_id}"
                if hasattr(self, fallback_key):
                    delattr(self, fallback_key)
            self.state.save_if_dirty()

        # Update last known map
        self._last_session_map = current_map

        return current_map

    async def _monitor_loop(self) -> None:
        """Background loop for checking session updates.

        Uses simple async polling with aiofiles for non-blocking I/O.
        """
        logger.info("Session monitor started, polling every %ss", self.poll_interval)

        # Clean up all stale sessions on startup
        await self._cleanup_all_stale_sessions()
        # Initialize last known session_map
        self._last_session_map = await self._load_current_session_map()

        while self._running:
            try:
                # Load hook-based session map updates
                await self._session_manager.load_session_map()

                # Detect session_map changes and cleanup replaced/removed sessions
                current_map = await self._detect_and_cleanup_changes()
                active_session_ids = set(current_map.values())

                # Check for new messages (all I/O is async)
                new_messages = await self.check_for_updates(active_session_ids)

                for msg in new_messages:
                    status = "complete" if msg.is_complete else "streaming"
                    preview = msg.text[:80] + ("..." if len(msg.text) > 80 else "")
                    logger.info("[%s] session=%s: %s", status, msg.session_id, preview)
                    if self._message_callback:
                        try:
                            await self._message_callback(msg)
                        except Exception as e:
                            logger.error(f"Message callback error: {e}")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(self.poll_interval)

        logger.info("Session monitor stopped")

    def start(self) -> None:
        if self._running:
            logger.warning("Monitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self.state.save()
        logger.info("Session monitor stopped and state saved")
