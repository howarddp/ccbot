"""Claude Code backend — wraps existing Claude-specific logic.

Encapsulates:
  - JSONL transcript parsing (via TranscriptParser)
  - ~/.claude/projects/ directory scanning
  - Claude Code UI patterns and status spinners
  - SessionStart hook installation and processing
  - Headless ``claude -p`` execution
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import aiofiles

from ..terminal_parser import UIPattern, UI_PATTERNS, STATUS_SPINNERS
from ..transcript_parser import TranscriptParser
from ..utils import read_cwd_from_jsonl
from .base import HookResult, SessionFileInfo, TmuxCliBackend

logger = logging.getLogger(__name__)

# Subprocess timeout for claude -p
_SUBPROCESS_TIMEOUT_S = 300


class ClaudeBackend(TmuxCliBackend):
    """Backend for Claude Code CLI."""

    name = "Claude Code"
    agent_type = "claude"
    default_command = "claude"
    process_pattern = "claude"
    config_dir = Path.home() / ".claude"
    projects_path = Path.home() / ".claude" / "projects"
    settings_file = Path.home() / ".claude" / "settings.json"
    env_unset_var = "CLAUDECODE"
    startup_ready_pattern = r"^>"  # Claude Code shows ">" prompt when ready
    startup_timeout = 10.0

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
        return TranscriptParser.parse_line(line)

    @staticmethod
    def parse_transcript_entries(
        entries: list[dict],
        pending_tools: dict[str, Any] | None = None,
        no_notify_active: bool = False,
    ) -> tuple[list, dict, bool]:
        return TranscriptParser.parse_entries(
            entries,
            pending_tools=pending_tools or {},
            no_notify_active=no_notify_active,
        )

    # --- Session file discovery ---

    def find_session_file(self, session_id: str, cwd: str = "") -> Path | None:
        if not session_id:
            return None

        # Direct path construction from cwd
        if cwd:
            encoded_cwd = cwd.replace("/", "-")
            direct_path = self.projects_path / encoded_cwd / f"{session_id}.jsonl"
            if direct_path.exists():
                return direct_path

        # Fallback: glob search
        if self.projects_path.exists():
            matches = list(self.projects_path.glob(f"*/{session_id}.jsonl"))
            if matches:
                return matches[0]

        return None

    async def scan_session_files(self, active_cwds: set[str]) -> list[SessionFileInfo]:
        sessions: list[SessionFileInfo] = []

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
                                SessionFileInfo(
                                    session_id=session_id,
                                    file_path=file_path,
                                )
                            )

                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("Error reading index %s: %s", index_file, e)

            # Pick up un-indexed .jsonl files
            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    session_id = jsonl_file.stem
                    if session_id in indexed_ids:
                        continue

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
                        SessionFileInfo(
                            session_id=session_id,
                            file_path=jsonl_file,
                        )
                    )
            except OSError as e:
                logger.debug("Error scanning jsonl files in %s: %s", project_dir, e)

        return sessions

    # --- Terminal UI ---

    def get_ui_patterns(self) -> list[UIPattern]:
        return UI_PATTERNS

    def get_status_spinners(self) -> frozenset[str]:
        return STATUS_SPINNERS

    # --- Headless execution ---

    async def run_headless(self, prompt: str, cwd: Path) -> str:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            prompt,
            "--dangerously-skip-permissions",
            "--output-format",
            "text",
            "--no-session-persistence",
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_SUBPROCESS_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise

        return stdout_bytes.decode("utf-8", errors="replace")

    # --- Hook ---

    def install_hook(self) -> int:
        from ..hook import _install_hook

        return _install_hook()

    def process_hook_event(self, payload: dict) -> HookResult | None:
        session_id = payload.get("session_id", "")
        cwd = payload.get("cwd", "")
        event = payload.get("hook_event_name", "")

        if not session_id or event != "SessionStart":
            return None
        if not self._is_valid_session_id(session_id):
            return None
        if cwd and not os.path.isabs(cwd):
            return None

        return HookResult(
            session_id=session_id,
            cwd=cwd,
            window_key="",  # Filled in by hook_main
            window_name="",
        )
