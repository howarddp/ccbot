"""Tests for workspace-aware user profile functions in persona/profile.py."""

from pathlib import Path

import pytest

from baobaobot.persona.profile import (
    UserProfile,
    _profile_cache,
    _serialize_user_profile,
    create_user_profile,
    read_user_profile_raw_resolved,
    read_user_profile_resolved,
    read_user_profile_with_source,
    resolve_user_profile_path,
    write_user_profile,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear profile cache before each test."""
    _profile_cache.clear()
    yield
    _profile_cache.clear()


@pytest.fixture
def users_dir(tmp_path: Path) -> Path:
    d = tmp_path / "shared" / "users"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspace_test"
    d.mkdir(parents=True)
    return d


def _write_profile(path: Path, name: str = "Alice", lang: str = "en-US") -> None:
    """Helper to write a minimal profile file."""
    profile = UserProfile(name=name, language=lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_user_profile(profile), encoding="utf-8")


class TestResolveUserProfilePath:
    def test_shared_fallback(self, users_dir: Path, workspace_dir: Path) -> None:
        """Without local override, resolves to shared path."""
        _write_profile(users_dir / "123.md")
        path, is_local = resolve_user_profile_path(users_dir, 123, workspace_dir)
        assert path == users_dir / "123.md"
        assert is_local is False

    def test_local_priority(self, users_dir: Path, workspace_dir: Path) -> None:
        """Local .persona/<uid>.md takes priority over shared."""
        _write_profile(users_dir / "123.md", name="Shared Alice")
        local = workspace_dir / ".persona" / "123.md"
        _write_profile(local, name="Local Alice")

        path, is_local = resolve_user_profile_path(users_dir, 123, workspace_dir)
        assert path == local
        assert is_local is True

    def test_no_workspace_dir(self, users_dir: Path) -> None:
        """workspace_dir=None always resolves to shared."""
        _write_profile(users_dir / "123.md")
        path, is_local = resolve_user_profile_path(users_dir, 123, None)
        assert path == users_dir / "123.md"
        assert is_local is False

    def test_local_not_exists_fallback(
        self, users_dir: Path, workspace_dir: Path
    ) -> None:
        """When local doesn't exist, falls back to shared even with workspace_dir."""
        _write_profile(users_dir / "123.md")
        # .persona/ exists but no profile file
        (workspace_dir / ".persona").mkdir(parents=True, exist_ok=True)

        path, is_local = resolve_user_profile_path(users_dir, 123, workspace_dir)
        assert path == users_dir / "123.md"
        assert is_local is False


class TestReadUserProfileResolved:
    def test_read_shared(self, users_dir: Path, workspace_dir: Path) -> None:
        _write_profile(users_dir / "123.md", name="Shared")
        profile = read_user_profile_resolved(users_dir, 123, workspace_dir)
        assert profile.name == "Shared"

    def test_read_local(self, users_dir: Path, workspace_dir: Path) -> None:
        _write_profile(users_dir / "123.md", name="Shared")
        local = workspace_dir / ".persona" / "123.md"
        _write_profile(local, name="Local")
        profile = read_user_profile_resolved(users_dir, 123, workspace_dir)
        assert profile.name == "Local"

    def test_neither_exists(self, users_dir: Path, workspace_dir: Path) -> None:
        profile = read_user_profile_resolved(users_dir, 999, workspace_dir)
        assert profile == UserProfile()


class TestReadUserProfileWithSource:
    def test_source_shared(self, users_dir: Path, workspace_dir: Path) -> None:
        _write_profile(users_dir / "123.md")
        _, source = read_user_profile_with_source(users_dir, 123, workspace_dir)
        assert source == "shared"

    def test_source_local(self, users_dir: Path, workspace_dir: Path) -> None:
        _write_profile(users_dir / "123.md")
        local = workspace_dir / ".persona" / "123.md"
        _write_profile(local)
        _, source = read_user_profile_with_source(users_dir, 123, workspace_dir)
        assert source == "local"

    def test_source_shared_when_not_exists(
        self, users_dir: Path, workspace_dir: Path
    ) -> None:
        _, source = read_user_profile_with_source(users_dir, 999, workspace_dir)
        assert source == "shared"


class TestReadUserProfileRawResolved:
    def test_returns_raw_content(self, users_dir: Path, workspace_dir: Path) -> None:
        _write_profile(users_dir / "123.md", name="Alice")
        raw = read_user_profile_raw_resolved(users_dir, 123, workspace_dir)
        assert "Alice" in raw
        assert "# User" in raw

    def test_returns_empty_for_missing(
        self, users_dir: Path, workspace_dir: Path
    ) -> None:
        raw = read_user_profile_raw_resolved(users_dir, 999, workspace_dir)
        assert raw == ""


class TestWriteUserProfile:
    def test_copy_on_write_to_workspace(
        self, users_dir: Path, workspace_dir: Path
    ) -> None:
        """With workspace_dir, writes to .persona/<uid>.md."""
        _write_profile(users_dir / "123.md", name="Shared Alice", lang="en-US")

        updated = write_user_profile(
            users_dir, 123, workspace_dir=workspace_dir, language="zh-TW"
        )
        assert updated.language == "zh-TW"
        assert updated.name == "Shared Alice"  # inherited from shared

        # Local file was created
        local = workspace_dir / ".persona" / "123.md"
        assert local.is_file()
        assert "zh-TW" in local.read_text()

        # Shared file unchanged
        shared_content = (users_dir / "123.md").read_text()
        assert "en-US" in shared_content

    def test_write_to_shared(self, users_dir: Path) -> None:
        """Without workspace_dir, writes to shared users/<uid>.md."""
        create_user_profile(users_dir, 123, "Alice")
        updated = write_user_profile(users_dir, 123, name="Bob")
        assert updated.name == "Bob"
        content = (users_dir / "123.md").read_text()
        assert "Bob" in content

    def test_cache_cleared(self, users_dir: Path, workspace_dir: Path) -> None:
        """write_user_profile should invalidate _profile_cache."""
        create_user_profile(users_dir, 123, "Alice")
        assert 123 in _profile_cache

        write_user_profile(users_dir, 123, workspace_dir=workspace_dir, name="Bob")
        assert 123 not in _profile_cache

    def test_copy_on_write_reads_from_local_if_exists(
        self, users_dir: Path, workspace_dir: Path
    ) -> None:
        """If local already exists, reads from local (not shared) before updating."""
        _write_profile(users_dir / "123.md", name="Shared", lang="en-US")
        local = workspace_dir / ".persona" / "123.md"
        _write_profile(local, name="Local", lang="ja-JP")

        updated = write_user_profile(
            users_dir, 123, workspace_dir=workspace_dir, name="Updated"
        )
        assert updated.name == "Updated"
        assert updated.language == "ja-JP"  # kept from local, not shared
