"""AGENTSOUL.md management — read, update, and validate agent personality and identity.

Merges the former SOUL.md (personality/tone/boundaries) and IDENTITY.md
(name/role/emoji/vibe) into a single AGENTSOUL.md file.

Key dataclass: AgentIdentity.
Key functions: read_agentsoul(), write_agentsoul(), read_identity(), update_identity().
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

# Reverse map: dataclass field → English label (for output)
_REVERSE_FIELD_MAP = {
    "name": "Name",
    "role": "Role",
    "emoji": "Emoji",
    "vibe": "Vibe",
}


@dataclass
class AgentIdentity:
    """Structured representation of identity fields in AGENTSOUL.md."""

    name: str = "BaoBao"
    role: str = "Personal AI Assistant"
    emoji: str = "🐾"
    vibe: str = "warm, dependable, sharp"


def read_agentsoul(shared_dir: Path) -> str:
    """Read AGENTSOUL.md content from the shared directory."""
    path = shared_dir / "AGENTSOUL.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_agentsoul(shared_dir: Path, content: str) -> None:
    """Write new content to AGENTSOUL.md."""
    path = shared_dir / "AGENTSOUL.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Updated AGENTSOUL.md")


def parse_identity(content: str) -> AgentIdentity:
    """Parse identity fields from AGENTSOUL.md content.

    Searches the entire content for **Key**: Value patterns,
    so it works with both the old IDENTITY.md format and the
    new AGENTSOUL.md format.
    """
    identity = AgentIdentity()
    for match in _FIELD_RE.finditer(content):
        key = match.group(1).strip()
        value = match.group(2).strip()
        field = _FIELD_MAP.get(key)
        if field and value:
            setattr(identity, field, value)
    return identity


def read_identity(shared_dir: Path) -> AgentIdentity:
    """Read and parse identity fields from AGENTSOUL.md."""
    content = read_agentsoul(shared_dir)
    if not content:
        return AgentIdentity()
    return parse_identity(content)


def update_identity(shared_dir: Path, **kwargs: str) -> AgentIdentity:
    """Update specific identity fields in AGENTSOUL.md.

    Updates the identity fields in the ## Identity section while
    preserving all other sections (Personality, Tone, Boundaries, etc.).

    Args:
        shared_dir: Path to shared directory.
        **kwargs: Fields to update (name, role, emoji, vibe).

    Returns:
        Updated AgentIdentity.
    """
    content = read_agentsoul(shared_dir)
    identity = parse_identity(content) if content else AgentIdentity()

    for field, value in kwargs.items():
        if hasattr(identity, field) and value:
            setattr(identity, field, value)

    # Rebuild the ## Identity section
    new_identity_lines = ["## Identity"]
    for field in ["name", "role", "emoji", "vibe"]:
        label = _REVERSE_FIELD_MAP[field]
        value = getattr(identity, field)
        new_identity_lines.append(f"- **{label}**: {value}")
    new_identity_section = "\n".join(new_identity_lines)

    if not content:
        # No existing file — write a minimal AGENTSOUL.md
        new_content = f"# Agent Soul\n\n{new_identity_section}\n"
    else:
        # Replace the existing ## Identity section
        identity_match = re.search(r"^## Identity\s*$", content, re.MULTILINE)
        if identity_match:
            # Find end of identity section (next ## or end)
            next_section = re.search(
                r"^## ", content[identity_match.end() :], re.MULTILINE
            )
            if next_section:
                end = identity_match.end() + next_section.start()
                new_content = (
                    content[: identity_match.start()]
                    + new_identity_section
                    + "\n\n"
                    + content[end:]
                )
            else:
                new_content = (
                    content[: identity_match.start()] + new_identity_section + "\n"
                )
        else:
            # No ## Identity section found — prepend after the first heading
            heading_match = re.search(r"^#\s+.+$", content, re.MULTILINE)
            if heading_match:
                insert_pos = heading_match.end()
                new_content = (
                    content[:insert_pos]
                    + "\n\n"
                    + new_identity_section
                    + "\n"
                    + content[insert_pos:]
                )
            else:
                new_content = f"# Agent Soul\n\n{new_identity_section}\n\n{content}"

    write_agentsoul(shared_dir, new_content)
    logger.info("Updated identity fields in AGENTSOUL.md: %s", kwargs)

    return identity
