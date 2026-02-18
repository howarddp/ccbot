"""Schedule computation: compute next run time from a CronSchedule."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from .types import CronSchedule

logger = logging.getLogger(__name__)


def _resolve_tz(tz_name: str) -> ZoneInfo | None:
    """Resolve timezone name to ZoneInfo, or None for invalid."""
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (KeyError, ValueError):
        logger.warning("Invalid timezone: %s", tz_name)
        return None


def compute_next_run(
    schedule: CronSchedule,
    after_ts: float,
    default_tz: str = "",
) -> float | None:
    """Compute the next run timestamp after `after_ts`.

    Returns Unix timestamp (seconds) or None if no future run exists.
    """
    if schedule.kind == "at":
        return _compute_at(schedule, after_ts, default_tz)
    elif schedule.kind == "every":
        return _compute_every(schedule, after_ts)
    elif schedule.kind == "cron":
        return _compute_cron(schedule, after_ts, default_tz)
    else:
        logger.warning("Unknown schedule kind: %s", schedule.kind)
        return None


def _compute_at(
    schedule: CronSchedule, after_ts: float, default_tz: str = ""
) -> float | None:
    """One-shot schedule: parse ISO time, return None if already past."""
    if not schedule.at:
        return None
    try:
        dt = datetime.fromisoformat(schedule.at)
        # If naive, use default_tz (consistent with cron expressions), fallback UTC
        if dt.tzinfo is None:
            tz = _resolve_tz(default_tz)
            dt = dt.replace(tzinfo=tz or timezone.utc)
        ts = dt.timestamp()
        return ts if ts > after_ts else None
    except ValueError:
        logger.warning("Invalid at time: %s", schedule.at)
        return None


def _compute_every(schedule: CronSchedule, after_ts: float) -> float | None:
    """Fixed interval schedule."""
    if schedule.every_seconds <= 0:
        return None
    return after_ts + schedule.every_seconds


def _compute_cron(
    schedule: CronSchedule,
    after_ts: float,
    default_tz: str,
) -> float | None:
    """Cron expression schedule with timezone support."""
    if not schedule.expr:
        return None

    tz_name = schedule.tz or default_tz
    tz = _resolve_tz(tz_name)

    try:
        if tz:
            # Convert after_ts to local time for croniter
            dt_after = datetime.fromtimestamp(after_ts, tz=tz)
            cron = croniter(schedule.expr, dt_after)
            next_dt = cron.get_next(datetime)
            return next_dt.timestamp()
        else:
            # Use UTC
            dt_after = datetime.fromtimestamp(after_ts, tz=timezone.utc)
            cron = croniter(schedule.expr, dt_after)
            next_dt = cron.get_next(datetime)
            return next_dt.timestamp()
    except (ValueError, KeyError) as e:
        logger.warning("Invalid cron expression '%s': %s", schedule.expr, e)
        return None
