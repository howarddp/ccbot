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
        for filename in ["AGENTSOUL.md", "AGENTS.md"]:
            assert (manager.shared_dir / filename).is_file()

    def test_idempotent(self, manager: WorkspaceManager) -> None:
        manager.init_shared()
        agentsoul = manager.shared_dir / "AGENTSOUL.md"
        agentsoul.write_text("custom content")

        manager.init_shared()  # Should NOT overwrite
        assert agentsoul.read_text() == "custom content"


class TestInitWorkspace:
    def test_creates_directories(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        assert manager.workspace_dir.is_dir()
        assert (manager.workspace_dir / "projects").is_dir()
        assert manager.memory_dir.is_dir()

    def test_creates_experience_dir(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        assert (manager.memory_dir / "experience").is_dir()

    def test_does_not_deploy_shared_templates(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        for filename in ["AGENTSOUL.md", "AGENTS.md"]:
            assert not (manager.workspace_dir / filename).is_file()

    def test_idempotent(self, manager: WorkspaceManager) -> None:
        manager.init_workspace()
        # Put a file in experience/ to verify it's preserved
        exp_dir = manager.memory_dir / "experience"
        (exp_dir / "topic.md").write_text("custom memory")

        manager.init_workspace()  # Should NOT delete existing files
        assert (exp_dir / "topic.md").read_text() == "custom memory"


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
