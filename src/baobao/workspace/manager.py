"""Workspace directory initialization and project management.

Manages the ~/.baobao/workspace/ directory structure:
  - init(): create directories, copy default template files, install bin/ scripts.
  - ensure_project(): symlink a project directory into workspace/projects/.
  - get_claude_work_dir(): resolve the directory where Claude Code should start.
  - list_projects(): enumerate linked projects.

Key class: WorkspaceManager.
"""

import logging
import shutil
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

# Template files bundled with the package
_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Bin scripts bundled with the package
_BIN_DIR = Path(__file__).parent / "bin"

# Files that are deployed to the workspace root
_TEMPLATE_FILES = [
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
    "MEMORY.md",
]

# Scripts that are deployed to ~/.baobao/bin/
_BIN_SCRIPTS = [
    "memory-search",
    "memory-list",
]


class WorkspaceManager:
    """Manages the BaoBao workspace directory structure."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.projects_dir = workspace_dir / "projects"
        self.memory_dir = workspace_dir / "memory"
        # bin/ lives under the baobao root (parent of workspace), shared across workspaces
        self.bin_dir = workspace_dir.parent / "bin"

    def init(self) -> None:
        """Initialize workspace directory structure and deploy default templates.

        Safe to call multiple times — only creates missing directories and files.
        Also installs bin/ scripts to the parent baobao directory.
        """
        # Create directory structure
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(exist_ok=True)
        self.memory_dir.mkdir(exist_ok=True)

        # Copy template files (only if they don't exist)
        for filename in _TEMPLATE_FILES:
            dest = self.workspace_dir / filename
            if not dest.exists():
                src = _TEMPLATES_DIR / filename
                if src.exists():
                    shutil.copy2(src, dest)
                    logger.info("Deployed template: %s", dest)
                else:
                    logger.warning("Template not found: %s", src)

        # Install bin/ scripts (always overwrite to keep up-to-date)
        self._install_bin_scripts()

        logger.debug("Workspace initialized at %s", self.workspace_dir)

    def _install_bin_scripts(self) -> None:
        """Copy bin/ scripts to ~/.baobao/bin/ and make them executable."""
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for script_name in _BIN_SCRIPTS:
            src = _BIN_DIR / script_name
            dest = self.bin_dir / script_name
            if src.exists():
                shutil.copy2(src, dest)
                # Ensure executable
                dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                logger.debug("Installed script: %s", dest)
            else:
                logger.warning("Script not found: %s", src)

    def ensure_project(self, project_path: str) -> Path:
        """Create a symlink in projects/ pointing to the actual project directory.

        Args:
            project_path: Absolute path to the project directory.

        Returns:
            The symlink path inside workspace/projects/.

        Raises:
            ValueError: If project_path doesn't exist or isn't a directory.
        """
        real_path = Path(project_path).expanduser().resolve()
        if not real_path.is_dir():
            raise ValueError(f"Not a directory: {project_path}")

        link_name = real_path.name
        link_path = self.projects_dir / link_name

        # Handle name collision — add suffix
        counter = 2
        while link_path.exists() and link_path.resolve() != real_path:
            link_path = self.projects_dir / f"{link_name}-{counter}"
            counter += 1

        if not link_path.exists():
            link_path.symlink_to(real_path)
            logger.info("Linked project: %s -> %s", link_path, real_path)

        return link_path

    def get_claude_work_dir(self, project_name: str | None = None) -> Path:
        """Get the directory where Claude Code should start.

        Args:
            project_name: Name of a linked project (directory name in projects/).
                          If None, returns the workspace root.

        Returns:
            Path to the working directory.

        Raises:
            ValueError: If project_name is given but doesn't exist.
        """
        if project_name is None:
            return self.workspace_dir

        project_path = self.projects_dir / project_name
        if not project_path.exists():
            raise ValueError(f"Project not found: {project_name}")

        return project_path

    def list_projects(self) -> list[str]:
        """List names of linked projects in workspace/projects/."""
        if not self.projects_dir.exists():
            return []
        return sorted(
            p.name
            for p in self.projects_dir.iterdir()
            if p.is_dir() or p.is_symlink()
        )
