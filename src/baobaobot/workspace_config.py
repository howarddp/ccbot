"""Per-workspace configuration via workspace.toml.

Provides persistent, workspace-scoped settings that survive session rebuilds.
Settings are stored in ``workspace.toml`` at the workspace root, alongside
``CLAUDE.md`` and ``memory/``.

Format::

    [workspace]
    agent_type = "gemini"

    [users.7022938281]
    verbosity = "quiet"
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILENAME = "workspace.toml"

# Valid values
_VALID_VERBOSITY = {"quiet", "normal", "verbose"}


def _read_toml(ws_dir: Path) -> dict[str, Any]:
    """Read workspace.toml, returning empty dict if missing or invalid."""
    path = ws_dir / _FILENAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def _write_toml(ws_dir: Path, data: dict[str, Any]) -> None:
    """Write workspace.toml atomically."""
    path = ws_dir / _FILENAME
    content = _serialize_toml(data)
    tmp = path.with_suffix(".toml.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning("Failed to write %s: %s", path, e)
        tmp.unlink(missing_ok=True)


def _serialize_toml(data: dict[str, Any]) -> str:
    """Serialize a simple nested dict to TOML format.

    Only supports the structure we use:
    - [workspace] section with string values
    - [users.<id>] sections with string values
    """
    lines: list[str] = []

    # [workspace] section
    ws = data.get("workspace")
    if ws and isinstance(ws, dict):
        lines.append("[workspace]")
        for k, v in ws.items():
            lines.append(f'{k} = "{v}"')
        lines.append("")

    # [users.<id>] sections
    users = data.get("users")
    if users and isinstance(users, dict):
        for uid, settings in sorted(users.items()):
            if isinstance(settings, dict) and settings:
                lines.append(f"[users.{uid}]")
                for k, v in settings.items():
                    lines.append(f'{k} = "{v}"')
                lines.append("")

    return "\n".join(lines)


# --- Public API ---


def get_agent_type(ws_dir: Path) -> str:
    """Get workspace backend type. Returns empty string for default."""
    data = _read_toml(ws_dir)
    return data.get("workspace", {}).get("agent_type", "")


def set_agent_type(ws_dir: Path, agent_type: str) -> None:
    """Set workspace backend type."""
    data = _read_toml(ws_dir)
    if "workspace" not in data:
        data["workspace"] = {}
    data["workspace"]["agent_type"] = agent_type
    _write_toml(ws_dir, data)


def get_verbosity(ws_dir: Path, user_id: int) -> str:
    """Get verbosity for a user in this workspace. Returns empty string if unset."""
    data = _read_toml(ws_dir)
    user_key = str(user_id)
    level = data.get("users", {}).get(user_key, {}).get("verbosity", "")
    if level and level not in _VALID_VERBOSITY:
        logger.warning("Invalid verbosity %r in %s for user %s", level, ws_dir, user_id)
        return ""
    return level


def set_verbosity(ws_dir: Path, user_id: int, level: str) -> None:
    """Set verbosity for a user in this workspace."""
    if level not in _VALID_VERBOSITY:
        raise ValueError(f"Invalid verbosity level: {level}")
    data = _read_toml(ws_dir)
    if "users" not in data:
        data["users"] = {}
    user_key = str(user_id)
    if user_key not in data["users"]:
        data["users"][user_key] = {}
    data["users"][user_key]["verbosity"] = level
    _write_toml(ws_dir, data)


def ensure_defaults(
    ws_dir: Path, agent_type: str, user_id: int, verbosity: str = "normal"
) -> None:
    """Create workspace.toml with defaults if missing, or fill in missing fields.

    Called on session creation to ensure every workspace has explicit settings.
    Does not overwrite existing values.
    """
    data = _read_toml(ws_dir)
    changed = False

    # [workspace] section
    if "workspace" not in data:
        data["workspace"] = {}
    if not data["workspace"].get("agent_type"):
        data["workspace"]["agent_type"] = agent_type
        changed = True

    # [users.<id>] section
    if "users" not in data:
        data["users"] = {}
    user_key = str(user_id)
    if user_key not in data["users"]:
        data["users"][user_key] = {}
    if not data["users"][user_key].get("verbosity"):
        data["users"][user_key]["verbosity"] = verbosity
        changed = True

    if changed:
        _write_toml(ws_dir, data)
