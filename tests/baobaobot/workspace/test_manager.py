"""Tests for workspace/manager.py — WorkspaceManager."""

from pathlib import Path

import pytest

from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    """Create a WorkspaceManager with a temporary directory."""
    wm = WorkspaceManager(tmp_path / "workspace")
    return wm


class TestWorkspaceInit:
    def test_creates_directories(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        assert workspace.workspace_dir.is_dir()
        assert workspace.projects_dir.is_dir()
        assert workspace.memory_dir.is_dir()

    def test_deploys_templates(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        for filename in ["SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md", "MEMORY.md"]:
            assert (workspace.workspace_dir / filename).is_file()

    def test_idempotent(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        # Modify a file
        soul = workspace.workspace_dir / "SOUL.md"
        soul.write_text("custom content")

        # Init again — should NOT overwrite
        workspace.init()
        assert soul.read_text() == "custom content"

    def test_template_content(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        soul = workspace.workspace_dir / "SOUL.md"
        content = soul.read_text()
        assert "Soul" in content


class TestEnsureProject:
    def test_creates_symlink(self, workspace: WorkspaceManager, tmp_path: Path) -> None:
        workspace.init()
        project = tmp_path / "my-project"
        project.mkdir()

        link = workspace.ensure_project(str(project))
        assert link.is_symlink()
        assert link.resolve() == project.resolve()
        assert link.name == "my-project"

    def test_idempotent(self, workspace: WorkspaceManager, tmp_path: Path) -> None:
        workspace.init()
        project = tmp_path / "my-project"
        project.mkdir()

        link1 = workspace.ensure_project(str(project))
        link2 = workspace.ensure_project(str(project))
        assert link1 == link2

    def test_name_collision(self, workspace: WorkspaceManager, tmp_path: Path) -> None:
        workspace.init()
        project1 = tmp_path / "a" / "my-project"
        project1.mkdir(parents=True)
        project2 = tmp_path / "b" / "my-project"
        project2.mkdir(parents=True)

        link1 = workspace.ensure_project(str(project1))
        link2 = workspace.ensure_project(str(project2))
        assert link1.name == "my-project"
        assert link2.name == "my-project-2"

    def test_invalid_path(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        with pytest.raises(ValueError, match="Not a directory"):
            workspace.ensure_project("/nonexistent/path")


class TestGetClaudeWorkDir:
    def test_no_project(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        assert workspace.get_claude_work_dir() == workspace.workspace_dir

    def test_with_project(self, workspace: WorkspaceManager, tmp_path: Path) -> None:
        workspace.init()
        project = tmp_path / "my-project"
        project.mkdir()
        workspace.ensure_project(str(project))

        result = workspace.get_claude_work_dir("my-project")
        assert result == workspace.projects_dir / "my-project"

    def test_missing_project(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        with pytest.raises(ValueError, match="Project not found"):
            workspace.get_claude_work_dir("nonexistent")


class TestListProjects:
    def test_empty(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        assert workspace.list_projects() == []

    def test_with_projects(self, workspace: WorkspaceManager, tmp_path: Path) -> None:
        workspace.init()
        for name in ["beta", "alpha"]:
            p = tmp_path / name
            p.mkdir()
            workspace.ensure_project(str(p))

        projects = workspace.list_projects()
        assert projects == ["alpha", "beta"]  # sorted


class TestBinScripts:
    def test_installs_scripts(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        assert workspace.bin_dir.is_dir()
        assert (workspace.bin_dir / "memory-search").is_file()
        assert (workspace.bin_dir / "memory-list").is_file()

    def test_scripts_are_executable(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        import stat

        for name in ["memory-search", "memory-list"]:
            script = workspace.bin_dir / name
            mode = script.stat().st_mode
            assert mode & stat.S_IXUSR  # owner executable

    def test_scripts_updated_on_reinit(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        script = workspace.bin_dir / "memory-search"
        script.write_text("old content")

        workspace.init()  # Should overwrite
        assert script.read_text() != "old content"

    def test_bin_dir_is_parent_of_workspace(self, workspace: WorkspaceManager) -> None:
        workspace.init()
        # bin/ should be sibling of workspace/, not inside it
        assert workspace.bin_dir.parent == workspace.workspace_dir.parent
