"""Tests for workspace/assembler.py — ClaudeMdAssembler."""

from pathlib import Path

import pytest

from baobaobot.workspace.assembler import ClaudeMdAssembler
from baobaobot.memory.utils import strip_frontmatter
from baobaobot.workspace.manager import WorkspaceManager


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Initialize shared + workspace dirs and return (shared_dir, workspace_dir)."""
    shared = tmp_path / "shared"
    workspace = tmp_path / "workspace_test"
    wm = WorkspaceManager(shared, workspace)
    wm.init_shared()
    wm.init_workspace()
    return shared, workspace


class TestAssemble:
    def test_contains_header(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "BaoBao Assistant" in content
        assert "auto-generated" in content.lower()

    def test_contains_all_sections(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Agent Soul (AGENTSOUL)" in content
        assert "Work Instructions (AGENTS)" in content

    def test_includes_agentsoul_from_shared(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        (shared / "AGENTSOUL.md").write_text(
            "# Agent Soul\n\n## Personality\n- Test personality"
        )
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Test personality" in content

    def test_does_not_embed_memory_content(self, dirs: tuple[Path, Path]) -> None:
        """Memory file *content* should NOT be embedded — only a listing."""
        shared, workspace = dirs
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "notes.md").write_text("Remember this secret detail")
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Remember this secret detail" not in content
        # But the listing should be present
        assert "notes" in content
        assert "Memory Context" in content

    def test_does_not_embed_daily_memories(self, dirs: tuple[Path, Path]) -> None:
        """Daily memory files should NOT be embedded."""
        shared, workspace = dirs
        from datetime import date

        today = date.today().isoformat()
        memory_dir = workspace / "memory"
        memory_dir.mkdir(exist_ok=True)
        (memory_dir / f"{today}.md").write_text("## Today\n- Something happened")

        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Recent Memories" not in content
        assert "Something happened" not in content

    def test_no_bin_dir_template_variable(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "{{BIN_DIR}}" not in content

    def test_locale_template_variable(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace, locale="zh-TW")
        content = assembler.assemble()
        assert "{{LOCALE}}" not in content
        assert "zh-TW" in content


class TestWrite:
    def test_creates_claude_md(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert (workspace / "CLAUDE.md").is_file()

    def test_claude_md_has_full_content(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        content = (workspace / "CLAUDE.md").read_text()
        assert "BaoBao Assistant" in content
        assert "Agent Soul" in content

    def test_no_baobaobot_md(self, dirs: tuple[Path, Path]) -> None:
        """BAOBAOBOT.md should not be created — everything goes in CLAUDE.md."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert not (workspace / "BAOBAOBOT.md").exists()

    def test_removes_legacy_baobaobot_md(self, dirs: tuple[Path, Path]) -> None:
        """write() should delete a pre-existing BAOBAOBOT.md."""
        shared, workspace = dirs
        legacy = workspace / "BAOBAOBOT.md"
        legacy.write_text("old content")
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert not legacy.exists()
        assert (workspace / "CLAUDE.md").is_file()


class TestNeedsRebuild:
    def test_true_when_no_output(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assert assembler.needs_rebuild() is True

    def test_false_after_write(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

    def test_true_after_shared_source_change(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()

        import time

        time.sleep(0.01)
        (shared / "AGENTSOUL.md").write_text("# Agent Soul\n\nUpdated")
        assert assembler.needs_rebuild() is True

    def test_experience_dir_change_triggers_rebuild(
        self, dirs: tuple[Path, Path]
    ) -> None:
        """Adding/removing experience files triggers rebuild (listing changed)."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()

        import time

        time.sleep(0.01)
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "notes.md").write_text("New topic")
        assert assembler.needs_rebuild() is True

    def test_daily_memory_change_does_not_trigger_rebuild(
        self, dirs: tuple[Path, Path]
    ) -> None:
        """Daily memory changes should NOT trigger rebuild."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()

        import time

        time.sleep(0.01)
        memory_dir = workspace / "memory"
        (memory_dir / "2026-02-15.md").write_text("- thing")
        assert assembler.needs_rebuild() is False


class TestFrontmatterStripping:
    def test_strips_frontmatter_from_source(self, dirs: tuple[Path, Path]) -> None:
        """YAML frontmatter in source files should be stripped during assembly."""
        shared, workspace = dirs
        (shared / "AGENTSOUL.md").write_text(
            "---\ntitle: Soul\ntags: [test]\n---\n# Agent Soul\n\n- Personality"
        )
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "- Personality" in content
        assert "title: Soul" not in content
        assert "tags: [test]" not in content

    def test_no_frontmatter_unchanged(self, dirs: tuple[Path, Path]) -> None:
        """Files without frontmatter should be read as-is."""
        shared, workspace = dirs
        (shared / "AGENTSOUL.md").write_text("# Agent Soul\n\n- Personality")
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "- Personality" in content

    def test_strip_frontmatter_function(self) -> None:
        text = "---\ndate: 2026-02-15\ntags: []\n---\n## Content\n- thing\n"
        result = strip_frontmatter(text)
        assert "---" not in result
        assert "## Content" in result

    def test_strip_frontmatter_no_frontmatter(self) -> None:
        text = "## Content\n- thing\n"
        assert strip_frontmatter(text) == text


class TestExperienceListing:
    def test_lists_experience_files(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "project-architecture.md").write_text("Architecture details")
        (exp_dir / "user-preferences.md").write_text("User prefs")

        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Memory Context" in content
        assert "project-architecture" in content
        assert "user-preferences" in content

    def test_no_listing_when_no_experience(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "## Memory Context" not in content
        assert "Experience Topics" not in content

    def test_no_listing_when_experience_dir_empty(
        self, dirs: tuple[Path, Path]
    ) -> None:
        shared, workspace = dirs
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "## Memory Context" not in content
        assert "Experience Topics" not in content

    def test_listing_sorted_alphabetically(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        exp_dir = workspace / "memory" / "experience"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "zebra-topic.md").write_text("Z")
        (exp_dir / "alpha-topic.md").write_text("A")

        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        alpha_pos = content.index("alpha-topic")
        zebra_pos = content.index("zebra-topic")
        assert alpha_pos < zebra_pos


class TestUserProfilesSection:
    """Tests for embedded user profiles in CLAUDE.md."""

    def test_profiles_embedded(self, dirs: tuple[Path, Path]) -> None:
        """Allowed user profiles are embedded in CLAUDE.md."""
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice\n")

        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset({111})
        )
        content = assembler.assemble()
        assert "## User Profiles" in content
        assert "### User 111" in content
        assert "Alice" in content

    def test_local_profile_preferred(self, dirs: tuple[Path, Path]) -> None:
        """Workspace-local profile takes priority over shared."""
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Shared Alice\n")

        persona = workspace / ".persona"
        persona.mkdir(parents=True, exist_ok=True)
        (persona / "111.md").write_text("# User\n\n- **Name**: Local Alice\n")

        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset({111})
        )
        content = assembler.assemble()
        assert "Local Alice" in content
        assert "Shared Alice" not in content

    def test_no_users_no_section(self, dirs: tuple[Path, Path]) -> None:
        """Without allowed_users, no User Profiles section."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "## User Profiles" not in content

    def test_empty_users_no_section(self, dirs: tuple[Path, Path]) -> None:
        """With empty allowed_users, no User Profiles section."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset()
        )
        content = assembler.assemble()
        assert "## User Profiles" not in content

    def test_missing_profile_skipped(self, dirs: tuple[Path, Path]) -> None:
        """Users without profile files are silently skipped."""
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice\n")

        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset({111, 222})
        )
        content = assembler.assemble()
        assert "### User 111" in content
        assert "### User 222" not in content

    def test_multiple_users_sorted(self, dirs: tuple[Path, Path]) -> None:
        """Multiple users are sorted by user ID."""
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "222.md").write_text("# User\n\n- **Name**: Bob\n")
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice\n")

        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset({111, 222})
        )
        content = assembler.assemble()
        alice_pos = content.index("### User 111")
        bob_pos = content.index("### User 222")
        assert alice_pos < bob_pos


class TestProfileMtimeRebuild:
    """Tests for profile file change detection in needs_rebuild."""

    def test_shared_profile_change_triggers_rebuild(
        self, dirs: tuple[Path, Path]
    ) -> None:
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice\n")

        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset({111})
        )
        assembler.write()
        assert assembler.needs_rebuild() is False

        import time

        time.sleep(0.01)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice Updated\n")
        assert assembler.needs_rebuild() is True

    def test_local_profile_created_triggers_rebuild(
        self, dirs: tuple[Path, Path]
    ) -> None:
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice\n")

        assembler = ClaudeMdAssembler(
            shared, workspace, allowed_users=frozenset({111})
        )
        assembler.write()
        assert assembler.needs_rebuild() is False

        import time

        time.sleep(0.01)
        persona = workspace / ".persona"
        persona.mkdir(parents=True, exist_ok=True)
        (persona / "111.md").write_text("# User\n\n- **Name**: Local Alice\n")
        assert assembler.needs_rebuild() is True

    def test_no_users_no_profile_rebuild(self, dirs: tuple[Path, Path]) -> None:
        """Without allowed_users, profile changes don't trigger rebuild."""
        shared, workspace = dirs
        users_dir = shared / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Alice\n")

        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

        import time

        time.sleep(0.01)
        (users_dir / "111.md").write_text("# User\n\n- **Name**: Updated\n")
        assert assembler.needs_rebuild() is False


def _local_agentsoul(workspace: Path) -> Path:
    """Return .persona/AGENTSOUL.md path, creating .persona/ if needed."""
    persona = workspace / ".persona"
    persona.mkdir(exist_ok=True)
    return persona / "AGENTSOUL.md"


class TestLocalAgentsoul:
    """Tests for workspace-local AGENTSOUL.md (copy-on-write via .persona/)."""

    def test_prefers_local_agentsoul(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        _local_agentsoul(workspace).write_text("# Local Soul\n\n- Local personality")
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Local personality" in content

    def test_falls_back_to_shared(self, dirs: tuple[Path, Path]) -> None:
        shared, workspace = dirs
        # No local AGENTSOUL.md — should use shared
        assembler = ClaudeMdAssembler(shared, workspace)
        content = assembler.assemble()
        assert "Agent Soul" in content

    def test_needs_rebuild_after_local_created(self, dirs: tuple[Path, Path]) -> None:
        """Creating a workspace-local AGENTSOUL.md triggers rebuild."""
        shared, workspace = dirs
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

        import time

        time.sleep(0.01)
        _local_agentsoul(workspace).write_text("# Local Soul\n")
        assert assembler.needs_rebuild() is True

    def test_needs_rebuild_after_local_deleted(self, dirs: tuple[Path, Path]) -> None:
        """Deleting the workspace-local AGENTSOUL.md triggers rebuild."""
        shared, workspace = dirs
        _local_agentsoul(workspace).write_text("# Local Soul\n")
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

        import time

        time.sleep(0.01)
        (workspace / ".persona" / "AGENTSOUL.md").unlink()
        assert assembler.needs_rebuild() is True

    def test_shared_change_ignored_when_local_exists(
        self, dirs: tuple[Path, Path]
    ) -> None:
        """When local exists, shared changes don't trigger rebuild."""
        shared, workspace = dirs
        _local_agentsoul(workspace).write_text("# Local Soul\n")
        assembler = ClaudeMdAssembler(shared, workspace)
        assembler.write()
        assert assembler.needs_rebuild() is False

        import time

        time.sleep(0.01)
        (shared / "AGENTSOUL.md").write_text("# Updated Shared\n")
        assert assembler.needs_rebuild() is False
