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


def _extract_tool_result(tc: dict) -> str:
    """Extract a readable result string from a Gemini toolCall entry."""
    # Try resultDisplay first (human-friendly)
    result_display = tc.get("resultDisplay")
    if result_display and isinstance(result_display, str):
        return result_display.strip()

    # Fall back to result array (functionResponse format)
    result = tc.get("result")
    if not result:
        return ""

    parts: list[str] = []
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                fr = item.get("functionResponse", {})
                resp = fr.get("response", {})
                output = resp.get("output", "")
                error = resp.get("error", "")
                if error:
                    parts.append(f"⚠️ {error}")
                elif output:
                    parts.append(str(output))
    elif isinstance(result, str):
        parts.append(result)

    return "\n".join(parts).strip()


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
            stripped = text.strip()
            _NO_NOTIFY_TAG = "[NO_NOTIFY]"
            # Detect [NO_NOTIFY] or [System prefix as implicit no_notify
            user_no_notify = stripped.startswith(_NO_NOTIFY_TAG) or stripped.startswith("[System")
            if stripped.startswith(_NO_NOTIFY_TAG):
                stripped = stripped[len(_NO_NOTIFY_TAG):].strip()
            entries.append(
                GeminiParsedEntry(
                    text=stripped, content_type="text", role="user",
                    no_notify=user_no_notify,
                )
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
            # Check for [NO_NOTIFY] and strip the tag from text
            stripped = text.strip()
            _NO_NOTIFY_TAG = "[NO_NOTIFY]"
            no_notify = stripped.startswith(_NO_NOTIFY_TAG)
            if no_notify:
                stripped = stripped[len(_NO_NOTIFY_TAG) :].strip()
            if stripped:
                entries.append(
                    GeminiParsedEntry(
                        text=stripped,
                        content_type="text",
                        role="assistant",
                        no_notify=no_notify,
                    )
                )

        # Tool calls (Gemini embeds tool calls inside the gemini message)
        tool_calls = msg.get("toolCalls", [])
        for tc in tool_calls:
            tool_name = tc.get("name", "unknown")
            tool_id = tc.get("id", "")
            args = tc.get("args", {})
            status = tc.get("status", "")
            display_name = tc.get("displayName", tool_name)
            description = tc.get("description", "")

            # Build tool_use summary text
            tool_use_parts = [f"**{display_name}**"]
            if description:
                tool_use_parts.append(description)
            # Show key args (compact)
            for key, val in args.items():
                val_str = str(val)
                if len(val_str) > 200:
                    val_str = val_str[:200] + "…"
                tool_use_parts.append(f"`{key}`: {val_str}")
            entries.append(
                GeminiParsedEntry(
                    text="\n".join(tool_use_parts),
                    content_type="tool_use",
                    role="assistant",
                    tool_use_id=tool_id,
                    tool_name=tool_name,
                )
            )

            # Tool result
            result_text = _extract_tool_result(tc)
            if result_text:
                entries.append(
                    GeminiParsedEntry(
                        text=result_text,
                        content_type="tool_result",
                        role="assistant",
                        tool_use_id=tool_id,
                        tool_name=tool_name,
                    )
                )
            elif status == "error":
                entries.append(
                    GeminiParsedEntry(
                        text="⚠️ Error",
                        content_type="tool_result",
                        role="assistant",
                        tool_use_id=tool_id,
                        tool_name=tool_name,
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
            is_recheck = msg.get("_recheck", False) if isinstance(msg, dict) else False
            all_entries = parse_gemini_message(msg)
            if is_recheck:
                # In-place update: only emit tool_use/tool_result (text already sent)
                all_entries = [
                    e for e in all_entries
                    if e.content_type in ("tool_use", "tool_result")
                ]
            parsed.extend(all_entries)

        # Apply no_notify_active state: propagate from user messages to
        # subsequent assistant entries (tool_use, tool_result, text).
        # A non-no_notify user message resets the state.
        for entry in parsed:
            if entry.role == "user":
                if entry.no_notify:
                    no_notify_active = True
                else:
                    no_notify_active = False
            elif entry.no_notify:
                no_notify_active = True

            if no_notify_active:
                entry.no_notify = True

        return parsed, {}, no_notify_active
