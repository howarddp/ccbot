"""Tests for schedule computation."""

import time

from baobaobot.cron.schedule import compute_next_run
from baobaobot.cron.types import CronSchedule


class TestComputeEvery:
    def test_basic_interval(self):
        s = CronSchedule(kind="every", every_seconds=300)
        now = 1000.0
        result = compute_next_run(s, now)
        assert result == 1300.0

    def test_zero_interval(self):
        s = CronSchedule(kind="every", every_seconds=0)
        assert compute_next_run(s, 1000.0) is None


class TestComputeAt:
    def test_future_time(self):
        # Use a far-future timestamp
        future = "2099-01-01T00:00:00+00:00"
        s = CronSchedule(kind="at", at=future)
        result = compute_next_run(s, time.time())
        assert result is not None
        assert result > time.time()

    def test_past_time(self):
        past = "2000-01-01T00:00:00+00:00"
        s = CronSchedule(kind="at", at=past)
        result = compute_next_run(s, time.time())
        assert result is None

    def test_invalid_time(self):
        s = CronSchedule(kind="at", at="not-a-date")
        assert compute_next_run(s, 1000.0) is None

    def test_empty(self):
        s = CronSchedule(kind="at", at="")
        assert compute_next_run(s, 1000.0) is None

    def test_naive_uses_default_tz(self):
        # Naive datetime "2099-06-15T12:00" with Asia/Taipei should differ from UTC
        s = CronSchedule(kind="at", at="2099-06-15T12:00")
        result_utc = compute_next_run(s, 0.0, default_tz="")
        result_taipei = compute_next_run(s, 0.0, default_tz="Asia/Taipei")
        assert result_utc is not None
        assert result_taipei is not None
        # Taipei is UTC+8, so the timestamp should be 8 hours earlier
        assert result_taipei < result_utc

    def test_explicit_tz_ignores_default(self):
        # If datetime has explicit tz, default_tz should be ignored
        s = CronSchedule(kind="at", at="2099-06-15T12:00+00:00")
        result1 = compute_next_run(s, 0.0, default_tz="")
        result2 = compute_next_run(s, 0.0, default_tz="Asia/Taipei")
        assert result1 == result2


class TestComputeCron:
    def test_every_minute(self):
        s = CronSchedule(kind="cron", expr="* * * * *")
        now = time.time()
        result = compute_next_run(s, now)
        assert result is not None
        # Next minute should be within 60 seconds
        assert result <= now + 60

    def test_with_timezone(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Taipei")
        now = time.time()
        result = compute_next_run(s, now)
        assert result is not None
        assert result > now

    def test_invalid_expression(self):
        s = CronSchedule(kind="cron", expr="invalid")
        assert compute_next_run(s, 1000.0) is None

    def test_empty_expression(self):
        s = CronSchedule(kind="cron", expr="")
        assert compute_next_run(s, 1000.0) is None

    def test_invalid_timezone(self):
        s = CronSchedule(kind="cron", expr="* * * * *", tz="Invalid/Tz")
        now = time.time()
        # Should fall back to UTC
        result = compute_next_run(s, now)
        # With invalid tz, _resolve_tz returns None, uses UTC
        assert result is not None


class TestComputeUnknown:
    def test_unknown_kind(self):
        s = CronSchedule(kind="unknown")
        assert compute_next_run(s, 1000.0) is None
