"""Backend ABC — defines the interface all AI backends must implement.

Two layers:
  - Backend: top-level abstract base.
  - TmuxCliBackend: shared base for tmux-based CLI tools (Claude, Gemini, …).

Data classes:
  - SessionHandle: opaque handle returned by start_session.
  - HookResult: outcome of processing a hook event.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..terminal_parser import UIPattern


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SessionHandle:
    """Opaque reference to a running session."""

    window_id: str
    work_dir: str
    session_id: str = ""


@dataclass
class HookResult:
    """Outcome of processing a hook event."""

    session_id: str
    cwd: str
    window_key: str  # e.g. "baobaobot:@12"
    window_name: str
    agent_name: str = ""


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------


class Backend(ABC):
    """Abstract interface for an AI backend.

    Every backend must declare its ``name`` and ``agent_type``.
    """

    name: str  # Human-readable (e.g. "Claude Code")
    agent_type: str  # Settings key (e.g. "claude", "gemini")

    # --- Capability flags ---

    @property
    def supports_headless(self) -> bool:
        """True if the backend can run one-shot headless tasks."""
        return False

    @property
    def supports_hooks(self) -> bool:
        """True if the backend supports session-start hooks."""
        return False

    @property
    def has_tmux_window(self) -> bool:
        """True if the backend runs inside a tmux window."""
        return True

    # --- Hook (optional) ---

    def install_hook(self) -> int:
        """Install session hook.  Returns 0 on success."""
        return 0

    def process_hook_event(self, payload: dict) -> HookResult | None:
        """Process a hook event payload.  Returns None if not handled."""
        return None

    def get_ui_patterns(self) -> list[UIPattern]:
        """Return UIPatterns for interactive UI detection.  Empty by default."""
        return []

    def get_status_spinners(self) -> frozenset[str]:
        """Return spinner characters for status line detection.  Empty by default."""
        return frozenset()


# ---------------------------------------------------------------------------
# TmuxCliBackend — shared base for tmux-based CLIs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionFileInfo:
    """Describes a discovered session transcript file."""

    session_id: str
    file_path: Path


class TmuxCliBackend(Backend):
    """Shared implementation for CLI tools that run inside tmux.

    Subclasses must set class-level attributes and implement the abstract
    methods that differ between CLIs.
    """

    # --- Class-level defaults (override in subclasses) ---
    name: str = ""
    agent_type: str = ""
    default_command: str = ""
    process_pattern: str = ""  # pgrep -f pattern to find the CLI process
    config_dir: Path = Path()  # e.g. ~/.claude
    projects_path: Path = Path()  # where transcript files live
    settings_file: Path = Path()  # CLI settings file for hook installation
    env_unset_var: str | None = None  # env var to unset before launching
    is_full_json: bool = False  # True if transcript is a single JSON (not JSONL)
    startup_ready_pattern: str = ""  # regex to detect CLI is ready in tmux pane
    startup_timeout: float = 15.0  # max seconds to wait for CLI startup

    def __init__(self, cli_command: str = ""):
        self.cli_command = cli_command or self.default_command

    @property
    def has_tmux_window(self) -> bool:
        return True

    # --- Launch ---

    def get_launch_command(self) -> str:
        """Return the shell command to start the CLI in a tmux pane."""
        prefix = f"unset {self.env_unset_var} && " if self.env_unset_var else ""
        return f"{prefix}{self.cli_command}"

    # --- Transcript parsing (subclass must implement) ---

    @staticmethod
    @abstractmethod
    def parse_transcript_line(line: str) -> dict | None:
        """Parse a single line from the transcript file.

        Returns parsed dict or None if the line should be skipped.
        """

    @staticmethod
    @abstractmethod
    def parse_transcript_entries(
        entries: list[dict],
        pending_tools: dict[str, Any] | None = None,
        no_notify_active: bool = False,
    ) -> tuple[list, dict, bool]:
        """Parse a batch of transcript entries.

        Returns (parsed_entries, remaining_pending_tools, no_notify_active).
        """

    # --- Session file discovery (subclass must implement) ---

    @abstractmethod
    def find_session_file(self, session_id: str, cwd: str = "") -> Path | None:
        """Locate the transcript file for a given session_id.

        Args:
            session_id: The session identifier.
            cwd: Working directory hint for faster lookup.

        Returns:
            Path to the transcript file, or None.
        """

    @abstractmethod
    async def scan_session_files(self, active_cwds: set[str]) -> list[SessionFileInfo]:
        """Scan for session files matching active working directories.

        Args:
            active_cwds: Set of normalised cwd paths from live tmux windows.

        Returns:
            List of discovered session files.
        """

    # --- Terminal UI detection (override from Backend with concrete impl) ---

    @abstractmethod
    def get_ui_patterns(self) -> list[UIPattern]:
        """Return the list of UIPatterns for interactive UI detection."""

    @abstractmethod
    def get_status_spinners(self) -> frozenset[str]:
        """Return the set of spinner characters for status line detection."""

    # --- Headless execution (optional) ---

    async def run_headless(self, prompt: str, cwd: Path) -> str:
        """Run a one-shot headless task.  Returns stdout text.

        Only available when ``supports_headless`` is True.
        """
        raise NotImplementedError(f"{self.name} does not support headless execution")

    # --- Hook helpers ---

    def _is_valid_session_id(self, session_id: str) -> bool:
        """Check if session_id looks like a UUID."""
        return bool(
            re.match(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                session_id,
            )
        )
