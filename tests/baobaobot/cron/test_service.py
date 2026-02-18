"""Tests for CronService core logic."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from baobaobot.cron.service import CronService, _backoff_delay
from baobaobot.cron.types import (
    CronJob,
    CronJobState,
    CronSchedule,
    CronStoreFile,
)


class TestBackoffDelay:
    def test_zero_errors(self):
        assert _backoff_delay(0) == 0

    def test_first_error(self):
        assert _backoff_delay(1) == 30

    def test_second_error(self):
        assert _backoff_delay(2) == 60

    def test_many_errors(self):
        # Should cap at the last entry
        assert _backoff_delay(100) == 3600


class TestCronServiceCRUD:
    @pytest.fixture
    def service(self, tmp_path: Path) -> CronService:
        svc = CronService()
        ws_dir = tmp_path / "workspace_test"
        ws_dir.mkdir()
        svc._workspace_dirs["test"] = ws_dir
        svc._stores["test"] = CronStoreFile()
        return svc

    async def test_add_job(self, service: CronService):
        with patch("baobaobot.cron.service.config") as mock_config:
            mock_config.cron_default_tz = ""
            mock_config.workspace_dir_for.return_value = service._workspace_dirs["test"]
            job = await service.add_job(
                "test",
                "my-job",
                CronSchedule(kind="every", every_seconds=60),
                "hello",
            )
        assert job.name == "my-job"
        assert job.message == "hello"
        assert job.enabled is True
        assert len(job.id) == 8

    async def test_list_jobs(self, service: CronService):
        with patch("baobaobot.cron.service.config") as mock_config:
            mock_config.cron_default_tz = ""
            mock_config.workspace_dir_for.return_value = service._workspace_dirs["test"]
            await service.add_job(
                "test",
                "j1",
                CronSchedule(kind="every", every_seconds=60),
                "msg1",
            )
            await service.add_job(
                "test",
                "j2",
                CronSchedule(kind="every", every_seconds=120),
                "msg2",
            )
        jobs = await service.list_jobs("test")
        assert len(jobs) == 2

    async def test_remove_job(self, service: CronService):
        with patch("baobaobot.cron.service.config") as mock_config:
            mock_config.cron_default_tz = ""
            mock_config.workspace_dir_for.return_value = service._workspace_dirs["test"]
            job = await service.add_job(
                "test",
                "j",
                CronSchedule(kind="every", every_seconds=60),
                "msg",
            )
        ok = await service.remove_job("test", job.id)
        assert ok
        assert await service.list_jobs("test") == []

    async def test_remove_nonexistent(self, service: CronService):
        ok = await service.remove_job("test", "nonexistent")
        assert not ok

    async def test_enable_disable(self, service: CronService):
        with patch("baobaobot.cron.service.config") as mock_config:
            mock_config.cron_default_tz = ""
            mock_config.workspace_dir_for.return_value = service._workspace_dirs["test"]
            job = await service.add_job(
                "test",
                "j",
                CronSchedule(kind="every", every_seconds=60),
                "msg",
            )

            disabled = await service.disable_job("test", job.id)
            assert disabled is not None
            assert disabled.enabled is False

            enabled = await service.enable_job("test", job.id)
            assert enabled is not None
            assert enabled.enabled is True

    async def test_list_empty_workspace(self, service: CronService):
        jobs = await service.list_jobs("nonexistent")
        assert jobs == []


class TestCronServiceExecuteJob:
    """Tests for _execute_job logic."""

    @pytest.fixture
    def service(self, tmp_path: Path) -> CronService:
        svc = CronService()
        ws_dir = tmp_path / "workspace_test"
        ws_dir.mkdir()
        svc._workspace_dirs["test"] = ws_dir
        svc._stores["test"] = CronStoreFile()
        return svc

    async def test_execute_job_success(self, service: CronService):
        job = CronJob(
            id="abc",
            name="test",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="hello",
            enabled=True,
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.iter_thread_bindings.return_value = [(1, 2, "@0")]
            mock_sm.get_display_name.return_value = "test"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_config.cron_default_tz = ""

            await service._execute_job("test", job)

        assert job.state.last_status == "ok"
        assert job.state.consecutive_errors == 0
        assert job.state.running_at is None
        assert job.state.last_run_at is not None

    async def test_execute_job_send_failure(self, service: CronService):
        job = CronJob(
            id="abc",
            name="test",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="hello",
            enabled=True,
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.iter_thread_bindings.return_value = [(1, 2, "@0")]
            mock_sm.get_display_name.return_value = "test"
            mock_sm.send_to_window = AsyncMock(return_value=(False, "window gone"))
            mock_config.cron_default_tz = ""

            await service._execute_job("test", job)

        assert job.state.last_status == "error"
        assert job.state.consecutive_errors == 1
        assert "send_to_window failed" in job.state.last_error

    async def test_execute_job_no_window(self, service: CronService):
        job = CronJob(
            id="abc",
            name="test",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="hello",
            enabled=True,
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
            patch.object(service, "_recreate_window", new=AsyncMock(return_value=None)),
        ):
            mock_sm.iter_thread_bindings.return_value = []
            mock_config.cron_default_tz = ""

            await service._execute_job("test", job)

        assert job.state.last_status == "error"
        assert "Cannot find or create window" in job.state.last_error

    async def test_execute_at_job_disables_after_run(self, service: CronService):
        job = CronJob(
            id="abc",
            name="once",
            schedule=CronSchedule(kind="at", at="2099-01-01T00:00:00+00:00"),
            message="do it",
            enabled=True,
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.iter_thread_bindings.return_value = [(1, 2, "@0")]
            mock_sm.get_display_name.return_value = "test"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_config.cron_default_tz = ""

            await service._execute_job("test", job)

        assert job.enabled is False
        assert job.state.next_run_at is None

    async def test_execute_job_with_cached_window_id(self, service: CronService):
        job = CronJob(
            id="abc",
            name="test",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="hello",
            enabled=True,
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_config.cron_default_tz = ""

            # Pass pre-resolved window_id — should skip _resolve_window
            await service._execute_job("test", job, window_id="@5")

        mock_sm.send_to_window.assert_called_once_with("@5", "hello")
        assert job.state.last_status == "ok"


class TestCronServiceExecuteDueJobs:
    """Tests for _execute_due_jobs logic."""

    @pytest.fixture
    def service(self, tmp_path: Path) -> CronService:
        svc = CronService()
        ws_dir = tmp_path / "workspace_test"
        ws_dir.mkdir()
        svc._workspace_dirs["test"] = ws_dir
        svc._stores["test"] = CronStoreFile()
        return svc

    async def test_executes_due_jobs(self, service: CronService):
        now = time.time()
        job = CronJob(
            id="abc",
            name="due-job",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="tick",
            enabled=True,
            state=CronJobState(next_run_at=now - 10),
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.iter_thread_bindings.return_value = [(1, 2, "@0")]
            mock_sm.get_display_name.return_value = "test"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_config.cron_default_tz = ""

            await service._execute_due_jobs()

        assert job.state.last_status == "ok"

    async def test_skips_disabled_jobs(self, service: CronService):
        now = time.time()
        job = CronJob(
            id="abc",
            name="disabled",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="skip",
            enabled=False,
            state=CronJobState(next_run_at=now - 10),
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.send_to_window = AsyncMock()
            mock_config.cron_default_tz = ""

            await service._execute_due_jobs()

        mock_sm.send_to_window.assert_not_called()

    async def test_skips_future_jobs(self, service: CronService):
        future = time.time() + 3600
        job = CronJob(
            id="abc",
            name="future",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="later",
            enabled=True,
            state=CronJobState(next_run_at=future),
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.send_to_window = AsyncMock()
            mock_config.cron_default_tz = ""

            await service._execute_due_jobs()

        mock_sm.send_to_window.assert_not_called()

    async def test_delete_after_run(self, service: CronService):
        now = time.time()
        job = CronJob(
            id="abc",
            name="once",
            schedule=CronSchedule(kind="at", at="2099-01-01T00:00:00+00:00"),
            message="do it",
            enabled=True,
            delete_after_run=True,
            state=CronJobState(next_run_at=now - 10),
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.iter_thread_bindings.return_value = [(1, 2, "@0")]
            mock_sm.get_display_name.return_value = "test"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_config.cron_default_tz = ""

            await service._execute_due_jobs()

        assert len(service._stores["test"].jobs) == 0

    async def test_clears_stuck_job(self, service: CronService):
        now = time.time()
        job = CronJob(
            id="abc",
            name="stuck",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="msg",
            enabled=True,
            state=CronJobState(
                running_at=now - 8000,
                next_run_at=now - 100,  # stuck > 7200s
            ),
        )
        service._stores["test"].jobs.append(job)

        with (
            patch("baobaobot.cron.service.session_manager") as mock_sm,
            patch("baobaobot.cron.service.config") as mock_config,
        ):
            mock_sm.send_to_window = AsyncMock()
            mock_config.cron_default_tz = ""

            await service._execute_due_jobs()

        assert job.state.running_at is None
        assert job.state.last_status == "error"
        assert "stuck" in job.state.last_error


class TestCronServiceProperties:
    def test_defaults(self):
        svc = CronService()
        assert svc.is_running is False
        assert svc.total_jobs == 0
        assert svc.workspace_count == 0
