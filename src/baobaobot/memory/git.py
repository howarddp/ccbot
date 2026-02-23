"""Git integration for memory directory — auto-commit on every write.

Provides ensure_git_repo() and commit_memory() to track all memory
file changes in a per-workspace git repository.

The git repo lives inside each workspace's memory/ directory.
memory.db and other derived files are excluded via .gitignore.

NOTE: ensure_git_repo(), commit_memory(), and _GITIGNORE_CONTENT are
duplicated in workspace/bin/_memory_common.py for standalone bin script use.
Keep both copies in sync when modifying.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GITIGNORE_CONTENT = """\
memory.db
memory.db-journal
memory.db-wal
memory.db-shm
__pycache__/
"""


def ensure_git_repo(memory_dir: Path) -> bool:
    """Initialize a git repo in memory_dir if one doesn't exist.

    Creates .gitignore for derived files (memory.db, __pycache__).

    Args:
        memory_dir: The memory/ directory path.

    Returns:
        True if repo was initialized or already exists, False on error.
    """
    if not memory_dir.is_dir():
        return False

    git_dir = memory_dir / ".git"
    if git_dir.exists():
        return True

    try:
        subprocess.run(
            ["git", "init"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.name", "baobaobot"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.email", "noreply@baobaobot"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        gitignore = memory_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        # Initial commit with .gitignore
        subprocess.run(
            ["git", "add", ".gitignore"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", "init: memory git tracking"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        logger.info("Initialized git repo in %s", memory_dir)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to initialize git repo in %s", memory_dir)
        return False


def commit_memory(memory_dir: Path, message: str) -> bool:
    """Stage all changes and commit in the memory directory.

    Auto-initializes the repo if needed. Silently returns if there
    are no changes to commit.

    Args:
        memory_dir: The memory/ directory path.
        message: Commit message describing the change.

    Returns:
        True if a commit was made, False otherwise (no changes or error).
    """
    if not memory_dir.is_dir():
        return False

    if not ensure_git_repo(memory_dir):
        return False

    try:
        subprocess.run(
            ["git", "add", "."],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            # No staged changes
            return False

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=memory_dir,
            capture_output=True,
            timeout=10,
        )
        logger.debug("Memory commit: %s", message)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to commit memory: %s", message)
        return False
