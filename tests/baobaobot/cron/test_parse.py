"""Tests for schedule parsing and formatting."""

from baobaobot.cron.parse import format_schedule, parse_schedule
from baobaobot.cron.types import CronSchedule


class TestParseSchedule:
    def test_cron_quoted(self):
        s, err = parse_schedule('"0 9 * * *"')
        assert err == ""
        assert s is not None
        assert s.kind == "cron"
        assert s.expr == "0 9 * * *"

    def test_cron_single_quoted(self):
        s, err = parse_schedule("'*/5 * * * *'")
        assert err == ""
        assert s is not None
        assert s.expr == "*/5 * * * *"

    def test_cron_unquoted(self):
        s, err = parse_schedule("0 9 * * *")
        assert err == ""
        assert s is not None
        assert s.kind == "cron"
        assert s.expr == "0 9 * * *"

    def test_every_minutes(self):
        s, err = parse_schedule("every:30m")
        assert err == ""
        assert s is not None
        assert s.kind == "every"
        assert s.every_seconds == 1800

    def test_every_hours(self):
        s, err = parse_schedule("every:2h")
        assert err == ""
        assert s is not None
        assert s.every_seconds == 7200

    def test_every_days(self):
        s, err = parse_schedule("every:1d")
        assert err == ""
        assert s is not None
        assert s.every_seconds == 86400

    def test_every_seconds(self):
        s, err = parse_schedule("every:30s")
        assert err == ""
        assert s is not None
        assert s.every_seconds == 30

    def test_at_iso(self):
        s, err = parse_schedule("at:2026-02-20T14:00")
        assert err == ""
        assert s is not None
        assert s.kind == "at"
        assert s.at == "2026-02-20T14:00"

    def test_empty(self):
        s, err = parse_schedule("")
        assert s is None
        assert "Empty" in err

    def test_invalid(self):
        s, err = parse_schedule("garbage")
        assert s is None
        assert "Unrecognized" in err


class TestFormatSchedule:
    def test_cron(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *")
        assert format_schedule(s) == "0 9 * * *"

    def test_cron_with_tz(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Taipei")
        assert format_schedule(s) == "0 9 * * * (Asia/Taipei)"

    def test_every_minutes(self):
        s = CronSchedule(kind="every", every_seconds=1800)
        assert format_schedule(s) == "every 30m"

    def test_every_hours(self):
        s = CronSchedule(kind="every", every_seconds=7200)
        assert format_schedule(s) == "every 2h"

    def test_every_days(self):
        s = CronSchedule(kind="every", every_seconds=86400)
        assert format_schedule(s) == "every 1d"

    def test_every_seconds(self):
        s = CronSchedule(kind="every", every_seconds=45)
        assert format_schedule(s) == "every 45s"

    def test_at(self):
        s = CronSchedule(kind="at", at="2026-02-20T14:00")
        assert format_schedule(s) == "at 2026-02-20T14:00"
