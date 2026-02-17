"""SOUL.md management — read, update, and validate personality definitions.

The soul file defines core personality traits, tone, and behavioral boundaries
that shape how Claude Code interacts with the user.

Key functions: read_soul(), write_soul().
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def read_soul(workspace_dir: Path) -> str:
    """Read SOUL.md content from the workspace."""
    soul_path = workspace_dir / "SOUL.md"
    try:
        return soul_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_soul(workspace_dir: Path, content: str) -> None:
    """Write new content to SOUL.md."""
    soul_path = workspace_dir / "SOUL.md"
    soul_path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Updated SOUL.md")
