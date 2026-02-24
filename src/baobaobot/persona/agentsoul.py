"""AGENTSOUL.md management — read, update, and validate agent personality and identity.

Merges the former SOUL.md (personality/tone/boundaries) and IDENTITY.md
(name/role/emoji/vibe) into a single AGENTSOUL.md file.

Supports per-workspace overrides via copy-on-write: when ``workspace_dir``
is given and contains its own ``AGENTSOUL.md``, that file takes precedence
over the shared one.

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


def resolve_agentsoul_path(
    shared_dir: Path, workspace_dir: Path | None = None
) -> tuple[Path, bool]:
    """Resolve which AGENTSOUL.md to read.

    The workspace-local copy lives in ``.persona/AGENTSOUL.md`` (hidden
    directory) so Claude Code does not read it as a separate instructions
    file — the content is already embedded in the assembled ``CLAUDE.md``.

    Returns:
        (path, is_local) — *is_local* is True when the workspace has its own copy.
    """
    if workspace_dir is not None:
        local = workspace_dir / ".persona" / "AGENTSOUL.md"
        if local.is_file():
            return local, True
    return shared_dir / "AGENTSOUL.md", False


def read_agentsoul(shared_dir: Path, workspace_dir: Path | None = None) -> str:
    """Read AGENTSOUL.md, preferring a workspace-local copy when present."""
    path, _ = resolve_agentsoul_path(shared_dir, workspace_dir)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_agentsoul_with_source(
    shared_dir: Path, workspace_dir: Path | None = None
) -> tuple[str, str]:
    """Read AGENTSOUL.md and report its origin.

    Returns:
        (content, "local" | "shared")
    """
    path, is_local = resolve_agentsoul_path(shared_dir, workspace_dir)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""
    return content, "local" if is_local else "shared"


def write_agentsoul(
    shared_dir: Path, content: str, workspace_dir: Path | None = None
) -> None:
    """Write new content to AGENTSOUL.md.

    When *workspace_dir* is given the file is written there (copy-on-write);
    otherwise it goes to the shared directory.
    """
    if workspace_dir is not None:
        persona_dir = workspace_dir / ".persona"
        persona_dir.mkdir(exist_ok=True)
        path = persona_dir / "AGENTSOUL.md"
    else:
        path = shared_dir / "AGENTSOUL.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Updated AGENTSOUL.md at %s", path)


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


def read_identity(shared_dir: Path, workspace_dir: Path | None = None) -> AgentIdentity:
    """Read and parse identity fields from AGENTSOUL.md."""
    content = read_agentsoul(shared_dir, workspace_dir)
    if not content:
        return AgentIdentity()
    return parse_identity(content)


def update_identity(
    shared_dir: Path, workspace_dir: Path | None = None, **kwargs: str
) -> AgentIdentity:
    """Update specific identity fields in AGENTSOUL.md.

    Uses copy-on-write: reads from the effective source (workspace-local if
    present, otherwise shared), applies the changes, and writes to
    *workspace_dir* when given — or *shared_dir* otherwise.

    Args:
        shared_dir: Path to shared directory.
        workspace_dir: Optional workspace directory for per-workspace override.
        **kwargs: Fields to update (name, role, emoji, vibe).

    Returns:
        Updated AgentIdentity.
    """
    content = read_agentsoul(shared_dir, workspace_dir)
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

    write_agentsoul(shared_dir, new_content, workspace_dir=workspace_dir)
    logger.info("Updated identity fields in AGENTSOUL.md: %s", kwargs)

    return identity
