"""Gemini CLI transcript parser.

Parses Gemini's session JSON files (single JSON with messages array)
and produces ParsedEntry-compatible output for the downstream pipeline.

Gemini session format:
    {
        "sessionId": "uuid",
        "projectHash": "sha256-hex",
        "startTime": "ISO",
        "lastUpdated": "ISO",
        "messages": [
            {
                "id": "uuid",
                "timestamp": "ISO",
                "type": "user" | "gemini",
                "content": str | list[{"text": str}],
                "thoughts": [{"subject": str, "description": str}, ...],
                "tokens": {"inputTokens": int, "outputTokens": int},
                "model": "gemini-2.5-pro"
            },
            ...
        ]
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GeminiParsedEntry:
    """A single parsed entry from a Gemini transcript.

    Mirrors the interface of transcript_parser.ParsedEntry so downstream
    code (session_monitor) can treat them uniformly.
    """

    text: str
    content_type: str  # "text", "thinking", "tool_use", "tool_result"
    role: str  # "user" or "assistant"
    tool_use_id: str | None = None
    tool_name: str | None = None
    no_notify: bool = False


def parse_gemini_message(msg: dict) -> list[GeminiParsedEntry]:
    """Parse a single Gemini message into GeminiParsedEntry list.

    A single message may produce multiple entries (e.g. thinking + text).
    """
    entries: list[GeminiParsedEntry] = []
    msg_type = msg.get("type", "")
    content = msg.get("content", "")
    thoughts = msg.get("thoughts", [])

    if msg_type == "user":
        # User message
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict)]
            text = "\n".join(text_parts)
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)

        if text.strip():
            entries.append(
                GeminiParsedEntry(text=text.strip(), content_type="text", role="user")
            )

    elif msg_type == "gemini":
        # Thinking entries
        for thought in thoughts:
            subject = thought.get("subject", "")
            desc = thought.get("description", "")
            thinking_text = f"{subject}: {desc}" if subject else desc
            if thinking_text.strip():
                entries.append(
                    GeminiParsedEntry(
                        text=thinking_text.strip(),
                        content_type="thinking",
                        role="assistant",
                    )
                )

        # Main content
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict)]
            text = "\n".join(text_parts)
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)

        if text.strip():
            # Check for [NO_NOTIFY]
            no_notify = text.strip().startswith("[NO_NOTIFY]")
            entries.append(
                GeminiParsedEntry(
                    text=text.strip(),
                    content_type="text",
                    role="assistant",
                    no_notify=no_notify,
                )
            )

    return entries


class GeminiTranscriptParser:
    """Parser for Gemini CLI session JSON files.

    Unlike Claude's line-by-line JSONL, Gemini stores the entire session
    as a single JSON file with a ``messages`` array.  We track how many
    messages we've already processed via an index offset.
    """

    @staticmethod
    def parse_session_json(raw: str) -> dict | None:
        """Parse the full session JSON. Returns the parsed dict or None."""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def parse_line(line: str) -> dict | None:
        """Compatibility shim: Gemini doesn't use JSONL.

        For the monitor's incremental line reader this is a no-op.
        Gemini sessions are parsed as a whole via parse_full_session.
        """
        # Gemini files are not line-based JSONL, so this always returns None.
        # The actual parsing happens through parse_full_session.
        return None

    @staticmethod
    def parse_entries(
        entries: list[dict],
        pending_tools: dict[str, Any] | None = None,
        no_notify_active: bool = False,
    ) -> tuple[list[GeminiParsedEntry], dict[str, Any], bool]:
        """Parse a list of Gemini message dicts.

        Args:
            entries: List of raw message dicts from the session JSON.
            pending_tools: Unused (Gemini doesn't have tool pairing like Claude).
            no_notify_active: Whether [NO_NOTIFY] is active.

        Returns:
            (parsed_entries, remaining_pending_tools, no_notify_active)
        """
        parsed: list[GeminiParsedEntry] = []
        for msg in entries:
            parsed.extend(parse_gemini_message(msg))

        # Track NO_NOTIFY across polls
        for entry in parsed:
            if entry.no_notify:
                no_notify_active = True
            elif entry.role == "user":
                no_notify_active = False

        return parsed, {}, no_notify_active
