"""Tests for memory git integration."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from baobaobot.memory.git import commit_memory, ensure_git_repo


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    """Create a temporary memory directory."""
    d = tmp_path / "memory"
    d.mkdir()
    return d


class TestEnsureGitRepo:
    def test_init_new_repo(self, memory_dir: Path) -> None:
        assert ensure_git_repo(memory_dir) is True
        assert (memory_dir / ".git").exists()
        assert (memory_dir / ".gitignore").exists()

        gitignore = (memory_dir / ".gitignore").read_text()
        assert "memory.db" in gitignore
        assert "__pycache__/" in gitignore

    def test_idempotent(self, memory_dir: Path) -> None:
        assert ensure_git_repo(memory_dir) is True
        assert ensure_git_repo(memory_dir) is True

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        assert ensure_git_repo(tmp_path / "nonexistent") is False

    def test_preserves_existing_gitignore(self, memory_dir: Path) -> None:
        gitignore = memory_dir / ".gitignore"
        gitignore.write_text("custom\n")
        ensure_git_repo(memory_dir)
        assert gitignore.read_text() == "custom\n"


class TestCommitMemory:
    def test_commit_new_file(self, memory_dir: Path) -> None:
        (memory_dir / "daily").mkdir()
        (memory_dir / "daily" / "test.md").write_text("hello")

        assert commit_memory(memory_dir, "test: add file") is True

        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=memory_dir,
            capture_output=True,
            text=True,
        )
        assert "test: add file" in result.stdout

    def test_no_changes_returns_false(self, memory_dir: Path) -> None:
        ensure_git_repo(memory_dir)
        assert commit_memory(memory_dir, "nothing") is False

    def test_commit_modification(self, memory_dir: Path) -> None:
        f = memory_dir / "test.md"
        f.write_text("v1")
        commit_memory(memory_dir, "v1")

        f.write_text("v2")
        assert commit_memory(memory_dir, "v2") is True

        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=memory_dir,
            capture_output=True,
            text=True,
        )
        assert "v2" in result.stdout
        assert "v1" in result.stdout

    def test_commit_deletion(self, memory_dir: Path) -> None:
        f = memory_dir / "test.md"
        f.write_text("content")
        commit_memory(memory_dir, "add")

        f.unlink()
        assert commit_memory(memory_dir, "delete") is True

    def test_ignores_memory_db(self, memory_dir: Path) -> None:
        ensure_git_repo(memory_dir)
        (memory_dir / "memory.db").write_text("sqlite")
        assert commit_memory(memory_dir, "db") is False

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        assert commit_memory(tmp_path / "nope", "msg") is False

    @patch("baobaobot.memory.git.subprocess.run", side_effect=FileNotFoundError)
    def test_git_not_installed(self, mock_run: object, memory_dir: Path) -> None:
        assert commit_memory(memory_dir, "msg") is False


class TestSchemaSync:
    """Ensure _memory_common.py git functions stay in sync with memory/git.py."""

    def test_gitignore_content_matches(self) -> None:
        from baobaobot.memory.git import _GITIGNORE_CONTENT

        # Import from bin script's _memory_common
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_memory_common",
            Path(__file__).parents[3]
            / "src"
            / "baobaobot"
            / "workspace"
            / "bin"
            / "_memory_common.py",
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod._GITIGNORE_CONTENT == _GITIGNORE_CONTENT
