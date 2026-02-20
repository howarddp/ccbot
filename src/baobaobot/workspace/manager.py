"""Workspace directory initialization and project management.

Manages a two-tier layout:
  - Shared dir (config_dir): AGENTSOUL.md, AGENTS.md, bin/, users/
  - Per-topic workspace (workspace_<topic>): memory/EXPERIENCE.md, memory/, projects/, BAOBAOBOT.md

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

# Skill templates bundled with the package
_SKILLS_DIR = Path(__file__).parent / "skills"

# Files deployed to the shared dir (config_dir) — created only if missing
_SHARED_TEMPLATE_FILES = [
    "AGENTSOUL.md",
]

# System-managed files always overwritten from templates
_SHARED_SYNC_FILES = [
    "AGENTS.md",
]

# Files deployed to each per-topic workspace (dest_subdir, filename)
_WORKSPACE_TEMPLATE_FILES = [
    ("memory", "EXPERIENCE.md"),
]

# Scripts deployed to shared bin/
_BIN_SCRIPTS = [
    "memory-search",
    "memory-list",
    "memory-save",
    "cron-add",
    "cron-list",
    "cron-remove",
]

# Skills deployed to each per-topic workspace
_SKILL_NAMES = [
    "memory-search",
    "memory-list",
    "memory-save",
    "cron-add",
    "cron-list",
    "cron-remove",
]


class WorkspaceManager:
    """Manages the BaoBao workspace directory structure."""

    def __init__(self, shared_dir: Path, workspace_dir: Path) -> None:
        self.shared_dir = shared_dir
        self.workspace_dir = workspace_dir
        self.memory_dir = workspace_dir / "memory"
        self.bin_dir = shared_dir / "bin"

    def init_shared(self) -> None:
        """Initialize shared persona files and bin/ scripts.

        Safe to call multiple times — only creates missing files.
        """
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        for filename in _SHARED_TEMPLATE_FILES:
            dest = self.shared_dir / filename
            if not dest.exists():
                src = _TEMPLATES_DIR / filename
                if src.exists():
                    shutil.copy2(src, dest)
                    logger.info("Deployed shared template: %s", dest)
                else:
                    logger.warning("Template not found: %s", src)

        # System-managed files: always overwrite from templates
        for filename in _SHARED_SYNC_FILES:
            src = _TEMPLATES_DIR / filename
            dest = self.shared_dir / filename
            if src.exists():
                shutil.copy2(src, dest)
                logger.debug("Synced system file: %s", dest)

        # Create users/ directory for multi-user profiles
        (self.shared_dir / "users").mkdir(exist_ok=True)

        # Warn about legacy USER.md (replaced by per-user profiles in users/)
        legacy_user_md = self.shared_dir / "USER.md"
        if legacy_user_md.exists():
            logger.info(
                "Legacy USER.md found at %s (no longer used; "
                "per-user profiles are now in users/)",
                legacy_user_md,
            )

        self._install_bin_scripts()
        logger.debug("Shared files initialized at %s", self.shared_dir)

    def init_workspace(self) -> None:
        """Initialize per-topic workspace directory structure.

        Safe to call multiple times — only creates missing directories and files.
        Skill files (.claude/skills/) are always overwritten to stay current.
        """
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "projects").mkdir(exist_ok=True)
        self.memory_dir.mkdir(exist_ok=True)
        (self.memory_dir / "attachments").mkdir(exist_ok=True)
        (self.workspace_dir / "tmp").mkdir(exist_ok=True)

        for subdir, filename in _WORKSPACE_TEMPLATE_FILES:
            dest = self.workspace_dir / subdir / filename
            if not dest.exists():
                src = _TEMPLATES_DIR / filename
                if src.exists():
                    shutil.copy2(src, dest)
                    logger.info("Deployed workspace template: %s", dest)
                else:
                    logger.warning("Template not found: %s", src)

        self._install_skills()
        logger.debug("Workspace initialized at %s", self.workspace_dir)

    def _install_skills(self) -> None:
        """Deploy SKILL.md files to workspace/.claude/skills/ with resolved paths.

        Always overwrites existing files (like bin scripts, not like templates).
        """
        bin_path = str(self.bin_dir)
        for skill_name in _SKILL_NAMES:
            src = _SKILLS_DIR / skill_name / "SKILL.md"
            if not src.exists():
                logger.warning("Skill template not found: %s", src)
                continue
            dest_dir = self.workspace_dir / ".claude" / "skills" / skill_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            content = src.read_text(encoding="utf-8")
            content = content.replace("{{BIN_DIR}}", bin_path)
            dest = dest_dir / "SKILL.md"
            dest.write_text(content, encoding="utf-8")
            logger.debug("Installed skill: %s", dest)

    def _install_bin_scripts(self) -> None:
        """Copy bin/ scripts to shared_dir/bin/ and make them executable."""
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for script_name in _BIN_SCRIPTS:
            src = _BIN_DIR / script_name
            dest = self.bin_dir / script_name
            if src.exists():
                shutil.copy2(src, dest)
                # Ensure executable
                dest.chmod(
                    dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
                logger.debug("Installed script: %s", dest)
            else:
                logger.warning("Script not found: %s", src)
