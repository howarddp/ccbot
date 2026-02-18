"""Tests for workspace/manager.py — WorkspaceManager."""

from pathlib import Path

import pytest

from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def manager(tmp_path: Path) -> WorkspaceManager:
    """Create a WorkspaceManager with temporary shared and workspace dirs."""
    shared = tmp_path / "shared"
    workspace = tmp_path / "workspace_test"
    return WorkspaceManager(shared, workspace)


class TestInitShared:
    def test_creates_shared_dir(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        assert manager.shared_dir.is_dir()

    def test_deploys_shared_templates(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        for filename in ["SOUL.md", "IDENTITY.md", "AGENTS.md"]:
            assert (manager.shared_dir / filename).is_file()

    def test_does_not_deploy_memory_md(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        assert not (manager.shared_dir / "MEMORY.md").is_file()

    def test_idempotent(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        soul = manager.shared_dir / "SOUL.md"
        soul.write_text("custom content")

        manager.init_shared()  # Should NOT overwrite
        assert soul.read_text() == "custom content"


class TestInitWorkspace:
    def test_creates_directories(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        assert manager.workspace_dir.is_dir()
        assert manager.projects_dir.is_dir()
        assert manager.memory_dir.is_dir()

    def test_deploys_memory_md(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        assert (manager.workspace_dir / "MEMORY.md").is_file()

    def test_does_not_deploy_shared_templates(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        for filename in ["SOUL.md", "IDENTITY.md", "AGENTS.md"]:
            assert not (manager.workspace_dir / filename).is_file()

    def test_idempotent(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        memory = manager.workspace_dir / "MEMORY.md"
        memory.write_text("custom memory")

        manager.init_workspace()  # Should NOT overwrite
        assert memory.read_text() == "custom memory"


class TestEnsureProject:
    def test_creates_symlink(self, manager: WorkspaceManager, tmp_path: Path) -> None:
        manager.init_workspace()
        project = tmp_path / "my-project"
        project.mkdir()

        link = manager.ensure_project(str(project))
        assert link.is_symlink()
        assert link.resolve() == project.resolve()
        assert link.name == "my-project"

    def test_idempotent(self, manager: WorkspaceManager, tmp_path: Path) -> None:
        manager.init_workspace()
        project = tmp_path / "my-project"
        project.mkdir()

        link1 = manager.ensure_project(str(project))
        link2 = manager.ensure_project(str(project))
        assert link1 == link2

    def test_name_collision(self, manager: WorkspaceManager, tmp_path: Path) -> None:
        manager.init_workspace()
        project1 = tmp_path / "a" / "my-project"
        project1.mkdir(parents=True)
        project2 = tmp_path / "b" / "my-project"
        project2.mkdir(parents=True)

        link1 = manager.ensure_project(str(project1))
        link2 = manager.ensure_project(str(project2))
        assert link1.name == "my-project"
        assert link2.name == "my-project-2"

    def test_invalid_path(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        with pytest.raises(ValueError, match="Not a directory"):
            manager.ensure_project("/nonexistent/path")


class TestGetClaudeWorkDir:
    def test_no_project(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        assert manager.get_claude_work_dir() == manager.workspace_dir

    def test_with_project(self, manager: WorkspaceManager, tmp_path: Path) -> None:
        manager.init_workspace()
        project = tmp_path / "my-project"
        project.mkdir()
        manager.ensure_project(str(project))

        result = manager.get_claude_work_dir("my-project")
        assert result == manager.projects_dir / "my-project"

    def test_missing_project(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        with pytest.raises(ValueError, match="Project not found"):
            manager.get_claude_work_dir("nonexistent")


class TestListProjects:
    def test_empty(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        assert manager.list_projects() == []

    def test_with_projects(self, manager: WorkspaceManager, tmp_path: Path) -> None:
        manager.init_workspace()
        for name in ["beta", "alpha"]:
            p = tmp_path / name
            p.mkdir()
            manager.ensure_project(str(p))

        projects = manager.list_projects()
        assert projects == ["alpha", "beta"]  # sorted


class TestBinScripts:
    def test_installs_scripts(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        assert manager.bin_dir.is_dir()
        assert (manager.bin_dir / "memory-search").is_file()
        assert (manager.bin_dir / "memory-list").is_file()

    def test_scripts_are_executable(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        import stat

        for name in ["memory-search", "memory-list"]:
            script = manager.bin_dir / name
            mode = script.stat().st_mode
            assert mode & stat.S_IXUSR  # owner executable

    def test_scripts_updated_on_reinit(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        script = manager.bin_dir / "memory-search"
        script.write_text("old content")

        manager.init_shared()  # Should overwrite
        assert script.read_text() != "old content"

    def test_bin_dir_is_under_shared_dir(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        assert manager.bin_dir.parent == manager.shared_dir


class TestSkills:
    def test_installs_skills(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        manager.init_workspace()
        skills_dir = manager.workspace_dir / ".claude" / "skills"
        assert skills_dir.is_dir()
        for name in [
            "memory-search",
            "memory-list",
            "cron-add",
            "cron-list",
            "cron-remove",
        ]:
            assert (skills_dir / name / "SKILL.md").is_file()

    def test_skills_contain_resolved_paths(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        manager.init_workspace()
        skill_file = (
            manager.workspace_dir / ".claude" / "skills" / "memory-search" / "SKILL.md"
        )
        content = skill_file.read_text()
        assert "{{BIN_DIR}}" not in content
        assert str(manager.bin_dir) in content

    def test_skills_updated_on_reinit(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        manager.init_workspace()
        skill_file = (
            manager.workspace_dir / ".claude" / "skills" / "memory-search" / "SKILL.md"
        )
        skill_file.write_text("old content")

        manager.init_workspace()  # Should overwrite
        assert skill_file.read_text() != "old content"

    def test_skills_have_frontmatter(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        manager.init_workspace()
        skill_file = (
            manager.workspace_dir / ".claude" / "skills" / "memory-search" / "SKILL.md"
        )
        content = skill_file.read_text()
        assert content.startswith("---\n")
        assert "name: memory-search" in content
        assert "description:" in content
