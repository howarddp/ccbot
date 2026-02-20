"""Router implementations — factory for creating mode-specific routers."""

from __future__ import annotations

from ..router import Router


def create_router(mode: str) -> Router:
    """Create a Router for the given mode.

    Args:
        mode: "forum" or "group".

    Returns:
        A concrete Router instance.

    Raises:
        ValueError: If mode is unknown.
    """
    if mode == "forum":
        from .forum import ForumRouter

        return ForumRouter()
    if mode == "group":
        from .group import GroupRouter

        return GroupRouter()
    raise ValueError(f"Unknown router mode: {mode!r} (expected 'forum' or 'group')")
