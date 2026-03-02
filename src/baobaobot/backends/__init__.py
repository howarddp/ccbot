"""Backend factory — creates the appropriate Backend for an agent_type.

Usage:
    backend = create_backend(agent_type="claude", cli_command="claude")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Backend

# Registry: agent_type -> (module_path, class_name)
_BACKEND_REGISTRY: dict[str, tuple[str, str]] = {
    "claude": (".claude", "ClaudeBackend"),
    "gemini": (".gemini", "GeminiBackend"),
}


def create_backend(agent_type: str, cli_command: str = "") -> Backend:
    """Create a Backend instance for the given agent_type.

    Args:
        agent_type: Backend type name (e.g. "claude", "gemini").
        cli_command: Override for the CLI command.  If empty, uses the
                     backend's default.

    Raises:
        ValueError: If agent_type is not registered.
    """
    entry = _BACKEND_REGISTRY.get(agent_type)
    if entry is None:
        supported = ", ".join(sorted(_BACKEND_REGISTRY))
        raise ValueError(f"Unknown agent_type {agent_type!r}. Supported: {supported}")

    module_path, class_name = entry

    # Lazy import to avoid circular dependencies
    import importlib

    mod = importlib.import_module(module_path, package=__name__)
    cls = getattr(mod, class_name)

    if cli_command:
        return cls(cli_command=cli_command)
    return cls()
