"""USER.md management — parse and update user profile fields.

Parses the structured USER.md format into a UserProfile dataclass
and provides field-level update operations.

Supports both legacy single-user USER.md (read_profile, update_profile)
and multi-user profiles in users/<user_id>.md (create/read/update_user_profile).

Key class: UserProfile.
Key functions: parse_profile(), read_profile(), update_profile(),
    create_user_profile(), read_user_profile(), update_user_profile().
"""

from __future__ import annotations

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
    "稱呼": "nickname",  # legacy only
    "Telegram": "telegram",
    "時區": "timezone",
    "語言偏好": "language",
    "備註": "notes",
}

# Reverse map for serialization (multi-user profiles)
_REVERSE_MAP = {
    "name": "名字",
    "telegram": "Telegram",
    "timezone": "時區",
    "language": "語言偏好",
    "notes": "備註",
}

# Regex to match @[user_id] mention markers in Claude Code output
_MENTION_RE = re.compile(r"@\[(\d+)\]")

# In-memory cache: user_id → UserProfile (invalidated on writes)
_profile_cache: dict[int, UserProfile] = {}


@dataclass
class UserProfile:
    """Structured representation of USER.md."""

    name: str = "（待設定）"
    nickname: str = "（待設定）"  # legacy, used only by read_profile/update_profile
    telegram: str = ""
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
    context_match = re.search(r"## Context\s*\n(.*?)(?:\n## |\Z)", content, re.DOTALL)
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
    _legacy_reverse = {
        "name": "名字",
        "nickname": "稱呼",
        "timezone": "時區",
        "language": "語言偏好",
        "notes": "備註",
    }

    lines = ["# User", ""]
    for field in ["name", "nickname", "timezone", "language", "notes"]:
        label = _legacy_reverse.get(field, field)
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


# --- Multi-user profile functions ---


def _user_profile_path(users_dir: Path, user_id: int) -> Path:
    """Return the path to a user's profile file."""
    return users_dir / f"{user_id}.md"


def _serialize_user_profile(profile: UserProfile) -> str:
    """Serialize a UserProfile to markdown."""
    lines = ["# User", ""]
    for field in ["name", "telegram", "timezone", "language", "notes"]:
        label = _REVERSE_MAP.get(field, field)
        value = getattr(profile, field)
        lines.append(f"- **{label}**: {value}")

    lines.extend(["", "## Context"])
    if profile.context:
        lines.append(profile.context)
    else:
        lines.append("<!-- 用戶的興趣、進行中的專案、偏好 -->")

    return "\n".join(lines) + "\n"


def create_user_profile(
    users_dir: Path,
    user_id: int,
    name: str,
    telegram_username: str = "",
) -> UserProfile:
    """Create a new user profile file.

    Args:
        users_dir: Path to the shared users/ directory.
        user_id: Telegram user ID.
        name: Display name (from Telegram first_name).
        telegram_username: Telegram @username (without @).

    Returns:
        The created UserProfile.
    """
    users_dir.mkdir(parents=True, exist_ok=True)
    profile_path = _user_profile_path(users_dir, user_id)

    if profile_path.exists():
        return read_user_profile(users_dir, user_id)

    telegram_str = f"@{telegram_username}" if telegram_username else ""
    profile = UserProfile(
        name=name,
        telegram=telegram_str,
    )

    profile_path.write_text(_serialize_user_profile(profile), encoding="utf-8")
    _profile_cache[user_id] = profile
    logger.info("Created user profile: %s (%d)", name, user_id)
    return profile


def read_user_profile(users_dir: Path, user_id: int) -> UserProfile:
    """Read a user profile by Telegram user ID.

    Returns default UserProfile if file doesn't exist.
    Uses in-memory cache to avoid repeated file reads.
    """
    cached = _profile_cache.get(user_id)
    if cached is not None:
        return cached

    profile_path = _user_profile_path(users_dir, user_id)
    try:
        content = profile_path.read_text(encoding="utf-8")
        profile = parse_profile(content)
        _profile_cache[user_id] = profile
        return profile
    except OSError:
        return UserProfile()


def read_user_profile_raw(users_dir: Path, user_id: int) -> str:
    """Read raw user profile content."""
    profile_path = _user_profile_path(users_dir, user_id)
    try:
        return profile_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def update_user_profile(users_dir: Path, user_id: int, **kwargs: str) -> UserProfile:
    """Update specific fields in a user's profile.

    Args:
        users_dir: Path to the shared users/ directory.
        user_id: Telegram user ID.
        **kwargs: Fields to update (name, telegram, timezone, language, notes).

    Returns:
        Updated UserProfile.
    """
    # Bypass cache to read fresh from disk for updates
    _profile_cache.pop(user_id, None)
    profile = read_user_profile(users_dir, user_id)

    for field, value in kwargs.items():
        if hasattr(profile, field) and value:
            setattr(profile, field, value)

    profile_path = _user_profile_path(users_dir, user_id)
    profile_path.write_text(_serialize_user_profile(profile), encoding="utf-8")
    _profile_cache[user_id] = profile
    logger.info("Updated user profile %d: %s", user_id, kwargs)

    return profile


def user_profile_exists(users_dir: Path, user_id: int) -> bool:
    """Check if a user profile file exists."""
    if user_id in _profile_cache:
        return True
    return _user_profile_path(users_dir, user_id).exists()


def get_user_display_name(users_dir: Path, user_id: int) -> str | None:
    """Get a user's display name from their profile.

    Returns None if profile doesn't exist or name is unset.
    """
    if user_id not in _profile_cache:
        profile_path = _user_profile_path(users_dir, user_id)
        if not profile_path.exists():
            return None
    profile = read_user_profile(users_dir, user_id)
    if profile.name and profile.name != "（待設定）":
        return profile.name
    return None


def ensure_user_profile(
    users_dir: Path,
    user_id: int,
    name: str,
    telegram_username: str = "",
) -> UserProfile:
    """Ensure a user profile exists, creating it if needed.

    Returns the existing or newly created profile.
    """
    if user_profile_exists(users_dir, user_id):
        return read_user_profile(users_dir, user_id)
    return create_user_profile(users_dir, user_id, name, telegram_username)


def convert_user_mentions(text: str, users_dir: Path) -> str:
    """Convert @[user_id] markers to Telegram mention format.

    Replaces @[12345] with [Name](tg://user?id=12345) for MarkdownV2.
    Falls back to user_id if no profile or name is found.
    """

    def _replace_mention(match: re.Match[str]) -> str:
        uid = int(match.group(1))
        display = get_user_display_name(users_dir, uid)
        if not display:
            display = str(uid)
        return f"[{display}](tg://user?id={uid})"

    return _MENTION_RE.sub(_replace_mention, text)
