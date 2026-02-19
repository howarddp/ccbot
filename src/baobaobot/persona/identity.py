"""IDENTITY.md management — parse and update agent identity fields.

Parses the structured IDENTITY.md format into an AgentIdentity dataclass
and provides field-level update operations.

Key class: AgentIdentity.
Key functions: parse_identity(), update_identity(), read_identity_raw().
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Pattern for "- **key**: value" lines
_FIELD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.*)$", re.MULTILINE)

# Field name mapping (label → dataclass field)
# Accepts both English and Chinese keys for backward compatibility
_FIELD_MAP = {
    "Name": "name",
    "名字": "name",
    "Role": "role",
    "角色": "role",
    "Emoji": "emoji",
    "emoji": "emoji",
    "Vibe": "vibe",
    "氛圍": "vibe",
}


@dataclass
class AgentIdentity:
    """Structured representation of IDENTITY.md."""

    name: str = "BaoBao"
    role: str = "Personal AI Assistant"
    emoji: str = "🐾"
    vibe: str = "warm, dependable, sharp"


def parse_identity(content: str) -> AgentIdentity:
    """Parse IDENTITY.md markdown content into an AgentIdentity."""
    identity = AgentIdentity()
    for match in _FIELD_RE.finditer(content):
        key = match.group(1).strip()
        value = match.group(2).strip()
        field = _FIELD_MAP.get(key)
        if field and value:
            setattr(identity, field, value)
    return identity


def read_identity(workspace_dir: Path) -> AgentIdentity:
    """Read and parse IDENTITY.md from the workspace."""
    identity_path = workspace_dir / "IDENTITY.md"
    try:
        content = identity_path.read_text(encoding="utf-8")
        return parse_identity(content)
    except OSError:
        return AgentIdentity()


def read_identity_raw(workspace_dir: Path) -> str:
    """Read raw IDENTITY.md content."""
    identity_path = workspace_dir / "IDENTITY.md"
    try:
        return identity_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def update_identity(workspace_dir: Path, **kwargs: str) -> AgentIdentity:
    """Update specific fields in IDENTITY.md.

    Args:
        workspace_dir: Path to workspace root.
        **kwargs: Fields to update (name, role, emoji, vibe).

    Returns:
        Updated AgentIdentity.
    """
    identity = read_identity(workspace_dir)

    for field, value in kwargs.items():
        if hasattr(identity, field) and value:
            setattr(identity, field, value)

    # Rebuild the markdown content
    # Reverse map: field → English key for output
    _reverse_map = {
        "name": "Name",
        "role": "Role",
        "emoji": "Emoji",
        "vibe": "Vibe",
    }

    lines = ["# Identity", ""]
    for field in ["name", "role", "emoji", "vibe"]:
        label = _reverse_map.get(field, field)
        value = getattr(identity, field)
        lines.append(f"- **{label}**: {value}")

    content = "\n".join(lines) + "\n"
    identity_path = workspace_dir / "IDENTITY.md"
    identity_path.write_text(content, encoding="utf-8")
    logger.info("Updated IDENTITY.md: %s", kwargs)

    return identity
