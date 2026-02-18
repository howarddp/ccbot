"""Tests for cron data models and serialization."""

from baobaobot.cron.types import (
    CronJob,
    CronJobState,
    CronSchedule,
    CronStoreFile,
    WorkspaceMeta,
)


class TestCronSchedule:
    def test_cron_round_trip(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Taipei")
        d = s.to_dict()
        assert d == {"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Taipei"}
        s2 = CronSchedule.from_dict(d)
        assert s2.kind == "cron"
        assert s2.expr == "0 9 * * *"
        assert s2.tz == "Asia/Taipei"

    def test_every_round_trip(self):
        s = CronSchedule(kind="every", every_seconds=1800)
        d = s.to_dict()
        assert d == {"kind": "every", "every_seconds": 1800}
        s2 = CronSchedule.from_dict(d)
        assert s2.every_seconds == 1800

    def test_at_round_trip(self):
        s = CronSchedule(kind="at", at="2026-02-20T14:00")
        d = s.to_dict()
        assert d == {"kind": "at", "at": "2026-02-20T14:00"}
        s2 = CronSchedule.from_dict(d)
        assert s2.at == "2026-02-20T14:00"


class TestCronJobState:
    def test_round_trip(self):
        state = CronJobState(
            next_run_at=1000.0,
            last_run_at=900.0,
            last_status="ok",
            consecutive_errors=0,
        )
        d = state.to_dict()
        s2 = CronJobState.from_dict(d)
        assert s2.next_run_at == 1000.0
        assert s2.last_status == "ok"

    def test_defaults(self):
        state = CronJobState.from_dict({})
        assert state.next_run_at is None
        assert state.last_status == ""
        assert state.consecutive_errors == 0


class TestCronJob:
    def test_round_trip(self):
        job = CronJob(
            id="abc12345",
            name="test-job",
            schedule=CronSchedule(kind="every", every_seconds=60),
            message="hello",
            enabled=True,
            created_at=1000.0,
            updated_at=1000.0,
        )
        d = job.to_dict()
        j2 = CronJob.from_dict(d)
        assert j2.id == "abc12345"
        assert j2.name == "test-job"
        assert j2.schedule.kind == "every"
        assert j2.message == "hello"


class TestCronStoreFile:
    def test_round_trip(self):
        store = CronStoreFile(
            workspace_meta=WorkspaceMeta(user_id=123, thread_id=456, chat_id=789),
            jobs=[
                CronJob(
                    id="a1b2c3d4",
                    name="test",
                    schedule=CronSchedule(kind="cron", expr="0 9 * * *"),
                    message="hi",
                )
            ],
        )
        d = store.to_dict()
        s2 = CronStoreFile.from_dict(d)
        assert s2.workspace_meta.user_id == 123
        assert len(s2.jobs) == 1
        assert s2.jobs[0].id == "a1b2c3d4"

    def test_empty(self):
        store = CronStoreFile.from_dict({})
        assert store.jobs == []
        assert store.workspace_meta.user_id == 0
