"""USER.md management — parse and update user profile fields.

Parses the structured USER.md format into a UserProfile dataclass
and provides field-level update operations.

Key class: UserProfile.
Key functions: parse_profile(), read_profile(), update_profile().
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Pattern for "- **key**: value" lines
_FIELD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.*)$", re.MULTILINE)

# Field name mapping (Chinese → dataclass field)
_FIELD_MAP = {
    "名字": "name",
    "稱呼": "nickname",
    "時區": "timezone",
    "語言偏好": "language",
    "備註": "notes",
}


@dataclass
class UserProfile:
    """Structured representation of USER.md."""

    name: str = "（待設定）"
    nickname: str = "（待設定）"
    timezone: str = "Asia/Taipei"
    language: str = "繁體中文"
    notes: str = ""
    context: str = ""


def parse_profile(content: str) -> UserProfile:
    """Parse USER.md markdown content into a UserProfile."""
    profile = UserProfile()
    for match in _FIELD_RE.finditer(content):
        key = match.group(1).strip()
        value = match.group(2).strip()
        field = _FIELD_MAP.get(key)
        if field and value:
            setattr(profile, field, value)

    # Extract context section
    context_match = re.search(
        r"## Context\s*\n(.*?)(?:\n## |\Z)", content, re.DOTALL
    )
    if context_match:
        ctx = context_match.group(1).strip()
        # Strip HTML comments
        ctx = re.sub(r"<!--.*?-->", "", ctx, flags=re.DOTALL).strip()
        if ctx:
            profile.context = ctx

    return profile


def read_profile(workspace_dir: Path) -> UserProfile:
    """Read and parse USER.md from the workspace."""
    user_path = workspace_dir / "USER.md"
    try:
        content = user_path.read_text(encoding="utf-8")
        return parse_profile(content)
    except OSError:
        return UserProfile()


def read_profile_raw(workspace_dir: Path) -> str:
    """Read raw USER.md content."""
    user_path = workspace_dir / "USER.md"
    try:
        return user_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def update_profile(workspace_dir: Path, **kwargs: str) -> UserProfile:
    """Update specific fields in USER.md.

    Args:
        workspace_dir: Path to workspace root.
        **kwargs: Fields to update (name, nickname, timezone, language, notes).

    Returns:
        Updated UserProfile.
    """
    profile = read_profile(workspace_dir)

    for field, value in kwargs.items():
        if hasattr(profile, field) and value:
            setattr(profile, field, value)

    # Reverse map
    _reverse_map = {
        "name": "名字",
        "nickname": "稱呼",
        "timezone": "時區",
        "language": "語言偏好",
        "notes": "備註",
    }

    lines = ["# User", ""]
    for field in ["name", "nickname", "timezone", "language", "notes"]:
        label = _reverse_map.get(field, field)
        value = getattr(profile, field)
        lines.append(f"- **{label}**: {value}")

    lines.extend(["", "## Context"])
    if profile.context:
        lines.append(profile.context)
    else:
        lines.append("<!-- 持續觀察：用戶的興趣、進行中的專案、偏好 -->")

    content = "\n".join(lines) + "\n"
    user_path = workspace_dir / "USER.md"
    user_path.write_text(content, encoding="utf-8")
    logger.info("Updated USER.md: %s", kwargs)

    return profile
