"""CLAUDE.md assembly — composes the root CLAUDE.md from shared + workspace files.

Reads AGENTS.md, SOUL.md, IDENTITY.md, USER.md from shared_dir, and
MEMORY.md + recent daily memory files from workspace_dir, then writes a
single assembled CLAUDE.md in the workspace root.
Claude Code reads this file automatically when starting in the workspace.

Key class: ClaudeMdAssembler.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Section order in the assembled CLAUDE.md
# (filename, section_title, source)  — source is "shared" or "workspace"
_SECTION_ORDER = [
    ("AGENTS.md", "工作指令 (AGENTS)", "shared"),
    ("SOUL.md", "人格 (SOUL)", "shared"),
    ("IDENTITY.md", "身份 (IDENTITY)", "shared"),
    ("USER.md", "用戶資訊 (USER)", "shared"),
    ("MEMORY.md", "記憶 (MEMORY)", "workspace"),
]

_HEADER = """\
# BaoBao Assistant

> 此檔案由 BaoBaoClaude 自動生成，請勿手動編輯。
> 最後更新：{timestamp}
"""


class ClaudeMdAssembler:
    """Reads shared persona files and per-topic memory, assembles CLAUDE.md."""

    def __init__(
        self, shared_dir: Path, workspace_dir: Path, recent_days: int = 7
    ) -> None:
        self.shared_dir = shared_dir
        self.workspace_dir = workspace_dir
        self.memory_dir = workspace_dir / "memory"
        self.output_path = workspace_dir / "CLAUDE.md"
        self.recent_days = recent_days
        self._source_mtimes: dict[str, float] = {}

    def _read_file(self, path: Path) -> str:
        """Read a file, returning empty string if it doesn't exist."""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _resolve_source_dir(self, source: str) -> Path:
        """Return shared_dir or workspace_dir based on source tag."""
        return self.shared_dir if source == "shared" else self.workspace_dir

    def _get_recent_memories(self) -> str:
        """Collect content from recent daily memory files."""
        if not self.memory_dir.exists():
            return ""

        today = datetime.now().date()
        lines: list[str] = []

        for i in range(self.recent_days):
            date = today - timedelta(days=i)
            date_str = date.isoformat()
            memory_file = self.memory_dir / f"{date_str}.md"
            content = self._read_file(memory_file)
            if content:
                lines.append(f"### {date_str}")
                lines.append(content)
                lines.append("")

        return "\n".join(lines).strip()

    def assemble(self) -> str:
        """Build the full CLAUDE.md content from source files."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts: list[str] = [_HEADER.format(timestamp=timestamp)]

        for filename, section_title, source in _SECTION_ORDER:
            source_dir = self._resolve_source_dir(source)
            filepath = source_dir / filename
            content = self._read_file(filepath)
            if content:
                parts.append(f"---\n\n## {section_title}")
                parts.append(content)

        # Recent daily memories
        recent = self._get_recent_memories()
        if recent:
            parts.append("---\n\n## 近期記憶")
            parts.append(recent)

        result = "\n\n".join(parts) + "\n"
        # Replace template variables (safety net for old AGENTS.md with {{BIN_DIR}})
        result = result.replace("{{BIN_DIR}}", str(self.shared_dir / "bin"))
        return result

    def write(self) -> None:
        """Assemble and write CLAUDE.md to the workspace root."""
        content = self.assemble()
        self.output_path.write_text(content, encoding="utf-8")
        logger.info("Assembled CLAUDE.md at %s", self.output_path)

        # Update mtime cache
        self._update_mtimes()

    def _update_mtimes(self) -> None:
        """Cache modification times of source files."""
        self._source_mtimes = {}
        for filename, _, source in _SECTION_ORDER:
            source_dir = self._resolve_source_dir(source)
            filepath = source_dir / filename
            if filepath.exists():
                self._source_mtimes[f"{source}:{filename}"] = filepath.stat().st_mtime

        # Also track memory directory
        if self.memory_dir.exists():
            for f in self.memory_dir.glob("*.md"):
                key = f"memory/{f.name}"
                self._source_mtimes[key] = f.stat().st_mtime

    def needs_rebuild(self) -> bool:
        """Check if any source file has been modified since last assembly."""
        if not self.output_path.exists():
            return True

        if not self._source_mtimes:
            # No cached mtimes — need rebuild
            return True

        for filename, _, source in _SECTION_ORDER:
            source_dir = self._resolve_source_dir(source)
            filepath = source_dir / filename
            if filepath.exists():
                current_mtime = filepath.stat().st_mtime
                cached = self._source_mtimes.get(f"{source}:{filename}", 0)
                if current_mtime > cached:
                    return True

        # Check memory files
        if self.memory_dir.exists():
            for f in self.memory_dir.glob("*.md"):
                key = f"memory/{f.name}"
                current_mtime = f.stat().st_mtime
                cached = self._source_mtimes.get(key, 0)
                if current_mtime > cached:
                    return True

        return False


def rebuild_all_workspaces(
    shared_dir: Path, workspace_dirs: list[Path], recent_days: int = 7
) -> int:
    """Rebuild CLAUDE.md for all workspaces where sources have changed.

    Returns the number of workspaces rebuilt.
    """
    rebuilt = 0
    for ws in workspace_dirs:
        assembler = ClaudeMdAssembler(shared_dir, ws, recent_days)
        if assembler.needs_rebuild():
            assembler.write()
            rebuilt += 1
    return rebuilt
