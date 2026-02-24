"""Tests for _run_tmp_cleanup in CronService."""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from baobaobot.cron.service import (
    CronService,
    _TMP_DEFAULT_MAX_AGE_S,
    _TMP_VOICE_MAX_AGE_S,
)


@pytest.fixture
def service(tmp_path: Path) -> CronService:
    ws_dir = tmp_path / "workspace_test"
    ws_dir.mkdir()
    svc = CronService(
        session_manager=MagicMock(),
        tmux_manager=MagicMock(),
        cron_default_tz="",
        users_dir=tmp_path / "users",
        workspace_dir_for=lambda name: ws_dir,
        iter_workspace_dirs=lambda: [ws_dir],
    )
    return svc


@pytest.fixture
def ws_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace_test"
    ws.mkdir(exist_ok=True)
    return ws


def _create_file(path: Path, age_seconds: float) -> Path:
    """Create a file and set its mtime to now - age_seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test")
    old_time = time.time() - age_seconds
    os.utime(path, (old_time, old_time))
    return path


class TestRunTmpCleanup:
    def test_voice_deleted_after_7_days(self, service: CronService, ws_dir: Path):
        voice_dir = ws_dir / "tmp" / "voice"
        _create_file(voice_dir / "old.ogg", _TMP_VOICE_MAX_AGE_S + 3600)

        deleted = service._run_tmp_cleanup(ws_dir)

        assert deleted == 1
        assert not (voice_dir / "old.ogg").exists()

    def test_voice_kept_within_7_days(self, service: CronService, ws_dir: Path):
        voice_dir = ws_dir / "tmp" / "voice"
        _create_file(voice_dir / "recent.ogg", _TMP_VOICE_MAX_AGE_S - 3600)

        deleted = service._run_tmp_cleanup(ws_dir)

        assert deleted == 0
        assert (voice_dir / "recent.ogg").exists()

    def test_tmp_files_deleted_after_30_days(self, service: CronService, ws_dir: Path):
        tmp_dir = ws_dir / "tmp"
        _create_file(tmp_dir / "old_photo.jpg", _TMP_DEFAULT_MAX_AGE_S + 3600)

        deleted = service._run_tmp_cleanup(ws_dir)

        assert deleted == 1
        assert not (tmp_dir / "old_photo.jpg").exists()

    def test_tmp_files_kept_within_30_days(self, service: CronService, ws_dir: Path):
        tmp_dir = ws_dir / "tmp"
        _create_file(tmp_dir / "recent.jpg", _TMP_DEFAULT_MAX_AGE_S - 3600)

        deleted = service._run_tmp_cleanup(ws_dir)

        assert deleted == 0
        assert (tmp_dir / "recent.jpg").exists()

    def test_subdirs_not_deleted(self, service: CronService, ws_dir: Path):
        tmp_dir = ws_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        subdir = tmp_dir / "some_subdir"
        subdir.mkdir()
        # Put an old file inside to make the subdir have an old mtime
        old_time = time.time() - _TMP_DEFAULT_MAX_AGE_S - 3600
        os.utime(subdir, (old_time, old_time))

        deleted = service._run_tmp_cleanup(ws_dir)

        assert deleted == 0
        assert subdir.is_dir()

    def test_empty_tmp_no_error(self, service: CronService, ws_dir: Path):
        # tmp dir doesn't even exist
        deleted = service._run_tmp_cleanup(ws_dir)
        assert deleted == 0

    def test_mixed_files(self, service: CronService, ws_dir: Path):
        """Old voice + old tmp + recent voice + recent tmp."""
        tmp_dir = ws_dir / "tmp"
        voice_dir = tmp_dir / "voice"

        _create_file(voice_dir / "old.ogg", _TMP_VOICE_MAX_AGE_S + 3600)
        _create_file(voice_dir / "new.ogg", 3600)
        _create_file(tmp_dir / "old.jpg", _TMP_DEFAULT_MAX_AGE_S + 3600)
        _create_file(tmp_dir / "new.pdf", 3600)

        deleted = service._run_tmp_cleanup(ws_dir)

        assert deleted == 2
        assert not (voice_dir / "old.ogg").exists()
        assert (voice_dir / "new.ogg").exists()
        assert not (tmp_dir / "old.jpg").exists()
        assert (tmp_dir / "new.pdf").exists()
