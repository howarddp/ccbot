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

import locale
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Sentinel values for "not yet configured" name fields
NAME_NOT_SET_SENTINELS = frozenset({"（待設定）", "(not set)"})

# Module-level caches for detected timezone/language (avoid repeated I/O)
_cached_tz: str | None = None
_cached_lang: str | None = None


def _detect_timezone() -> str:
    """Detect system timezone from OS, fallback to Asia/Taipei."""
    global _cached_tz
    if _cached_tz is not None:
        return _cached_tz
    # TZ environment variable
    tz = os.environ.get("TZ")
    if tz and "/" in tz:
        _cached_tz = tz
        return tz
    # macOS/Linux: resolve /etc/localtime symlink
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            _cached_tz = link.split("zoneinfo/", 1)[1]
            return _cached_tz
    except OSError:
        pass
    # Linux: /etc/timezone file
    try:
        result = Path("/etc/timezone").read_text().strip()
        _cached_tz = result
        return result
    except OSError:
        pass
    _cached_tz = "Asia/Taipei"
    return _cached_tz


_TZ_LANG_MAP: dict[str, str] = {
    "Asia/Taipei": "繁體中文",
    "Asia/Hong_Kong": "繁體中文",
    "Asia/Shanghai": "简体中文",
    "Asia/Chongqing": "简体中文",
    "Asia/Tokyo": "日本語",
    "Asia/Seoul": "한국어",
}


def _detect_language() -> str:
    """Detect display language from system locale and timezone."""
    global _cached_lang
    if _cached_lang is not None:
        return _cached_lang
    lang = (locale.getlocale()[0] or "").lower()
    if lang.startswith("zh_tw") or lang.startswith("zh_hant"):
        _cached_lang = "繁體中文"
        return _cached_lang
    if lang.startswith("zh_cn") or lang.startswith("zh_hans"):
        _cached_lang = "简体中文"
        return _cached_lang
    if lang.startswith("ja"):
        _cached_lang = "日本語"
        return _cached_lang
    if lang.startswith("ko"):
        _cached_lang = "한국어"
        return _cached_lang
    # Fallback: infer from timezone (many Asian devs use en_US locale)
    if lang.startswith("en") or not lang:
        tz = _detect_timezone()
        tz_lang = _TZ_LANG_MAP.get(tz)
        if tz_lang:
            _cached_lang = tz_lang
            return _cached_lang
    _cached_lang = "English"
    return _cached_lang


# Pattern for "- **key**: value" lines
_FIELD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.*)$", re.MULTILINE)

# Field name mapping (label → dataclass field)
# Accepts both English and Chinese keys for backward compatibility
_FIELD_MAP = {
    "Name": "name",
    "名字": "name",
    "Nickname": "nickname",  # legacy only
    "稱呼": "nickname",  # legacy only
    "Telegram": "telegram",
    "Timezone": "timezone",
    "時區": "timezone",
    "Language": "language",
    "語言偏好": "language",
    "Notes": "notes",
    "備註": "notes",
}

# Reverse map for serialization (multi-user profiles)
_REVERSE_MAP = {
    "name": "Name",
    "telegram": "Telegram",
    "timezone": "Timezone",
    "language": "Language",
    "notes": "Notes",
}

# Regex to match @[user_id] mention markers in Claude Code output
_MENTION_RE = re.compile(r"@\[(\d+)\]")

# In-memory cache: user_id → UserProfile (invalidated on writes)
_profile_cache: dict[int, UserProfile] = {}


@dataclass
class UserProfile:
    """Structured representation of USER.md."""

    name: str = "(not set)"
    nickname: str = "(not set)"  # legacy, used only by read_profile/update_profile
    telegram: str = ""
    timezone: str = field(default_factory=_detect_timezone)
    language: str = field(default_factory=_detect_language)
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

    for attr, value in kwargs.items():
        if hasattr(profile, attr) and value:
            setattr(profile, attr, value)

    # Reverse map
    _legacy_reverse = {
        "name": "Name",
        "nickname": "Nickname",
        "timezone": "Timezone",
        "language": "Language",
        "notes": "Notes",
    }

    lines = ["# User", ""]
    for attr in ["name", "nickname", "timezone", "language", "notes"]:
        label = _legacy_reverse.get(attr, attr)
        value = getattr(profile, attr)
        lines.append(f"- **{label}**: {value}")

    lines.extend(["", "## Context"])
    if profile.context:
        lines.append(profile.context)
    else:
        lines.append(
            "<!-- Ongoing observations: interests, active projects, preferences -->"
        )

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
    for attr in ["name", "telegram", "timezone", "language", "notes"]:
        label = _REVERSE_MAP.get(attr, attr)
        value = getattr(profile, attr)
        lines.append(f"- **{label}**: {value}")

    lines.extend(["", "## Context"])
    if profile.context:
        lines.append(profile.context)
    else:
        lines.append("<!-- User interests, active projects, preferences -->")

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

    for attr, value in kwargs.items():
        if hasattr(profile, attr) and value:
            setattr(profile, attr, value)

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
    if profile.name and profile.name not in NAME_NOT_SET_SENTINELS:
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
