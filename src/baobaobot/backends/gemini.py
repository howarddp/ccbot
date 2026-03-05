"""Gemini CLI backend.

Handles:
  - ``gemini`` CLI launch in tmux
  - Session JSON parsing (single JSON with messages array)
  - ``~/.gemini/tmp/<hash>/chats/`` directory scanning
  - Gemini-specific terminal UI patterns
  - Hook installation into ``~/.gemini/settings.json``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import aiofiles

from ..terminal_parser import UIPattern
from .base import HookResult, SessionFileInfo, TmuxCliBackend
from .gemini_parser import GeminiTranscriptParser

logger = logging.getLogger(__name__)

# Gemini UI patterns for terminal detection
_GEMINI_UI_PATTERNS: list[UIPattern] = [
    # Gemini's "Action Required" permission prompt (shell commands, MCP tools)
    # Note: Gemini renders inside box-drawing chars (│ ... │), so avoid ^ anchors
    UIPattern(
        name="GeminiPermission",
        top=(re.compile(r"Action Required"),),
        bottom=(
            re.compile(r"suggest changes"),
            re.compile(r"\(esc\)"),
        ),
        min_gap=2,
    ),
    # Gemini's yes/no confirmation
    UIPattern(
        name="GeminiConfirm",
        top=(re.compile(r"Do you want to"),),
        bottom=(re.compile(r"\(y/n\)"),),
        min_gap=1,
    ),
    # Gemini sandbox warning
    UIPattern(
        name="GeminiSandbox",
        top=(re.compile(r"⚠\s+Warning"),),
        bottom=(re.compile(r"\(y/n\)"),),
        min_gap=1,
    ),
]

# Gemini uses these spinner characters
_GEMINI_STATUS_SPINNERS = frozenset(
    ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏", "●"]
)


class GeminiBackend(TmuxCliBackend):
    """Backend for Gemini CLI."""

    name = "Gemini CLI"
    agent_type = "gemini"
    default_command = "gemini"
    process_pattern = "gemini"
    config_dir = Path.home() / ".gemini"
    projects_path = Path.home() / ".gemini" / "tmp"
    settings_file = Path.home() / ".gemini" / "settings.json"
    env_unset_var = None  # Gemini doesn't need env var unsetting

    # Transcript format flag — Gemini uses full-JSON, not line-based JSONL
    is_full_json = True
    startup_ready_pattern = r"Type your message"  # Gemini shows this when ready
    startup_timeout = 15.0

    def __init__(self, cli_command: str = ""):
        super().__init__(cli_command)
        # Cache: session_id → file path (avoids reading every JSON)
        self._session_file_cache: dict[str, Path] = {}

    # Subprocess timeout for gemini -p
    _SUBPROCESS_TIMEOUT_S = 300

    # --- Capabilities ---

    @property
    def supports_headless(self) -> bool:
        return True

    @property
    def supports_hooks(self) -> bool:
        return True

    # --- Transcript parsing ---

    @staticmethod
    def parse_transcript_line(line: str) -> dict | None:
        return GeminiTranscriptParser.parse_line(line)

    @staticmethod
    def parse_transcript_entries(
        entries: list[dict],
        pending_tools: dict[str, Any] | None = None,
        no_notify_active: bool = False,
    ) -> tuple[list, dict, bool]:
        return GeminiTranscriptParser.parse_entries(
            entries,
            pending_tools=pending_tools,
            no_notify_active=no_notify_active,
        )

    # --- Session file discovery ---

    @staticmethod
    def _project_hash(cwd: str) -> str:
        """Compute the sha256 hash Gemini uses for project directories."""
        return hashlib.sha256(cwd.encode()).hexdigest()

    @staticmethod
    def _project_name_for_cwd(cwd: str) -> str | None:
        """Look up the project name from ~/.gemini/projects.json."""
        projects_file = Path.home() / ".gemini" / "projects.json"
        if not projects_file.exists():
            return None
        try:
            data = json.loads(projects_file.read_text())
            return data.get("projects", {}).get(cwd)
        except (json.JSONDecodeError, OSError):
            return None

    def _find_project_dirs(self, cwd: str) -> list[Path]:
        """Find all possible Gemini project directories for a cwd."""
        dirs: list[Path] = []

        project_name = self._project_name_for_cwd(cwd)
        if project_name:
            named_dir = self.projects_path / project_name
            if named_dir.is_dir():
                dirs.append(named_dir)

        hash_dir = self.projects_path / self._project_hash(cwd)
        if hash_dir.is_dir() and hash_dir not in dirs:
            dirs.append(hash_dir)

        return dirs

    def _scan_and_cache(self, chats_dir: Path) -> None:
        """Read session IDs from chat files and populate cache."""
        for f in chats_dir.glob("*.json"):
            if f in self._session_file_cache.values():
                continue
            try:
                data = json.loads(f.read_text())
                sid = data.get("sessionId", "")
                if sid:
                    self._session_file_cache[sid] = f
            except (json.JSONDecodeError, OSError):
                continue

    def find_session_file(self, session_id: str, cwd: str = "") -> Path | None:
        if not session_id:
            return None

        # Check cache first
        cached = self._session_file_cache.get(session_id)
        if cached and cached.exists():
            return cached

        # Scan project dirs for cwd
        if cwd:
            for project_dir in self._find_project_dirs(cwd):
                chats_dir = project_dir / "chats"
                if chats_dir.is_dir():
                    self._scan_and_cache(chats_dir)

            cached = self._session_file_cache.get(session_id)
            if cached and cached.exists():
                return cached

        # Fallback: scan all project dirs
        if self.projects_path.exists():
            for project_dir in self.projects_path.iterdir():
                if not project_dir.is_dir():
                    continue
                chats_dir = project_dir / "chats"
                if chats_dir.is_dir():
                    self._scan_and_cache(chats_dir)

        cached = self._session_file_cache.get(session_id)
        if cached and cached.exists():
            return cached
        return None

    async def scan_session_files(self, active_cwds: set[str]) -> list[SessionFileInfo]:
        sessions: list[SessionFileInfo] = []

        if not self.projects_path.exists():
            return sessions

        # Build name → cwd mapping from projects.json
        projects_file = Path.home() / ".gemini" / "projects.json"
        name_to_cwd: dict[str, str] = {}
        if projects_file.exists():
            try:
                data = json.loads(projects_file.read_text())
                for cwd, name in data.get("projects", {}).items():
                    name_to_cwd[name] = cwd
            except (json.JSONDecodeError, OSError):
                pass

        # Build hash → cwd mapping for active cwds
        hash_to_cwd: dict[str, str] = {}
        for cwd in active_cwds:
            hash_to_cwd[self._project_hash(cwd)] = cwd

        for project_dir in self.projects_path.iterdir():
            if not project_dir.is_dir():
                continue

            dir_name = project_dir.name
            matched_cwd: str | None = None

            if dir_name in name_to_cwd:
                cwd = name_to_cwd[dir_name]
                try:
                    norm_cwd = str(Path(cwd).resolve())
                except (OSError, ValueError):
                    norm_cwd = cwd
                if norm_cwd in active_cwds:
                    matched_cwd = norm_cwd

            if not matched_cwd and dir_name in hash_to_cwd:
                matched_cwd = hash_to_cwd[dir_name]

            if not matched_cwd:
                continue

            chats_dir = project_dir / "chats"
            if not chats_dir.is_dir():
                continue

            for chat_file in chats_dir.glob("*.json"):
                try:
                    async with aiofiles.open(chat_file, "r") as f:
                        content = await f.read()
                    data = json.loads(content)
                    session_id = data.get("sessionId", "")
                    if session_id:
                        self._session_file_cache[session_id] = chat_file
                        sessions.append(
                            SessionFileInfo(
                                session_id=session_id,
                                file_path=chat_file,
                            )
                        )
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("Error reading chat file %s: %s", chat_file, e)

        return sessions

    # --- Terminal UI ---

    def get_ui_patterns(self) -> list[UIPattern]:
        return _GEMINI_UI_PATTERNS

    def get_status_spinners(self) -> frozenset[str]:
        return _GEMINI_STATUS_SPINNERS

    # --- Headless execution ---

    async def run_headless(self, prompt: str, cwd: Path) -> str:
        """Run a one-shot headless task via ``gemini -p``.

        Uses ``--yolo`` for auto-approval (equivalent to Claude's
        ``--dangerously-skip-permissions``).
        """
        import asyncio as _asyncio

        proc = await _asyncio.create_subprocess_exec(
            self.cli_command,
            "-p",
            prompt,
            "--yolo",
            "--output-format",
            "text",
            cwd=str(cwd),
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await _asyncio.wait_for(
                proc.communicate(), timeout=self._SUBPROCESS_TIMEOUT_S
            )
        except _asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise

        return stdout_bytes.decode("utf-8", errors="replace")

    # --- Hook ---

    def install_hook(self) -> int:
        from ..hook import _install_gemini_hook

        return _install_gemini_hook()

    def process_hook_event(self, payload: dict) -> HookResult | None:
        # Gemini hooks use a different payload format
        session_id = payload.get("session_id", "")
        cwd = payload.get("cwd", "")
        event = payload.get("hook_event_name", payload.get("event", ""))

        if not session_id or event not in ("SessionStart", "session_start"):
            return None
        if not self._is_valid_session_id(session_id):
            return None
        if cwd and not os.path.isabs(cwd):
            return None

        return HookResult(
            session_id=session_id,
            cwd=cwd,
            window_key="",
            window_name="",
        )
