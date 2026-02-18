"""Tests for cron store persistence."""

import json
from pathlib import Path

import pytest

from baobaobot.cron.store import load_store, save_store, store_mtime, store_path
from baobaobot.cron.types import (
    CronJob,
    CronSchedule,
    CronStoreFile,
    WorkspaceMeta,
)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace_test"
    ws.mkdir()
    return ws


class TestStorePath:
    def test_path(self, workspace_dir: Path):
        p = store_path(workspace_dir)
        assert p == workspace_dir / "cron" / "jobs.json"


class TestLoadStore:
    def test_missing_file(self, workspace_dir: Path):
        store = load_store(workspace_dir)
        assert store.jobs == []
        assert store.version == 1

    def test_valid_file(self, workspace_dir: Path):
        cron_dir = workspace_dir / "cron"
        cron_dir.mkdir()
        data = CronStoreFile(
            workspace_meta=WorkspaceMeta(user_id=1),
            jobs=[
                CronJob(
                    id="test1",
                    name="job1",
                    schedule=CronSchedule(kind="every", every_seconds=60),
                    message="hi",
                )
            ],
        ).to_dict()
        (cron_dir / "jobs.json").write_text(json.dumps(data))

        store = load_store(workspace_dir)
        assert len(store.jobs) == 1
        assert store.jobs[0].id == "test1"
        assert store.workspace_meta.user_id == 1

    def test_corrupt_json(self, workspace_dir: Path):
        cron_dir = workspace_dir / "cron"
        cron_dir.mkdir()
        (cron_dir / "jobs.json").write_text("{invalid json")

        store = load_store(workspace_dir)
        assert store.jobs == []


class TestSaveStore:
    def test_save_and_reload(self, workspace_dir: Path):
        store = CronStoreFile(
            workspace_meta=WorkspaceMeta(user_id=42),
            jobs=[
                CronJob(
                    id="abc",
                    name="test",
                    schedule=CronSchedule(kind="cron", expr="0 9 * * *"),
                    message="morning",
                )
            ],
        )
        save_store(workspace_dir, store)

        reloaded = load_store(workspace_dir)
        assert len(reloaded.jobs) == 1
        assert reloaded.jobs[0].message == "morning"

    def test_creates_directory(self, workspace_dir: Path):
        store = CronStoreFile()
        save_store(workspace_dir, store)
        assert (workspace_dir / "cron" / "jobs.json").is_file()


class TestStoreMtime:
    def test_missing(self, workspace_dir: Path):
        assert store_mtime(workspace_dir) == 0.0

    def test_existing(self, workspace_dir: Path):
        save_store(workspace_dir, CronStoreFile())
        mt = store_mtime(workspace_dir)
        assert mt > 0
