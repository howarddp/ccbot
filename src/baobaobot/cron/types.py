"""Data models for the cron job system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""

    kind: str  # "cron" | "every" | "at"
    expr: str = ""  # cron: "0 9 * * *"
    tz: str = ""  # cron: timezone (default system tz)
    every_seconds: int = 0  # every: interval in seconds
    at: str = ""  # at: ISO 8601 string

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == "cron":
            d["expr"] = self.expr
            if self.tz:
                d["tz"] = self.tz
        elif self.kind == "every":
            d["every_seconds"] = self.every_seconds
        elif self.kind == "at":
            d["at"] = self.at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronSchedule:
        return cls(
            kind=data.get("kind", "cron"),
            expr=data.get("expr", ""),
            tz=data.get("tz", ""),
            every_seconds=data.get("every_seconds", 0),
            at=data.get("at", ""),
        )


@dataclass
class CronJobState:
    """Runtime state for a cron job."""

    next_run_at: float | None = None  # Unix timestamp (seconds)
    running_at: float | None = None
    last_run_at: float | None = None
    last_status: str = ""  # "ok" | "error" | "skipped"
    last_error: str = ""
    last_duration_s: float = 0.0
    consecutive_errors: int = 0
    last_summary_offset: int = 0  # JSONL byte offset at last summary
    last_summary_jsonl: str = (
        ""  # JSONL file path at last summary (detect session change)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "next_run_at": self.next_run_at,
            "running_at": self.running_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_duration_s": self.last_duration_s,
            "consecutive_errors": self.consecutive_errors,
            "last_summary_offset": self.last_summary_offset,
            "last_summary_jsonl": self.last_summary_jsonl,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronJobState:
        return cls(
            next_run_at=data.get("next_run_at"),
            running_at=data.get("running_at"),
            last_run_at=data.get("last_run_at"),
            last_status=data.get("last_status", ""),
            last_error=data.get("last_error", ""),
            last_duration_s=data.get("last_duration_s", 0.0),
            consecutive_errors=data.get("consecutive_errors", 0),
            last_summary_offset=data.get("last_summary_offset", 0),
            last_summary_jsonl=data.get("last_summary_jsonl", ""),
        )


@dataclass
class CronJob:
    """A single cron job definition with state."""

    id: str  # uuid4().hex[:8]
    name: str
    schedule: CronSchedule
    message: str = ""  # text to send to tmux
    enabled: bool = True
    delete_after_run: bool = False  # at type defaults True
    system: bool = False  # system-managed job (cannot be removed by user)
    creator_user_id: int = 0  # Telegram user ID of the job creator
    created_at: float = 0.0
    updated_at: float = 0.0
    state: CronJobState = field(default_factory=CronJobState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule.to_dict(),
            "message": self.message,
            "enabled": self.enabled,
            "delete_after_run": self.delete_after_run,
            "system": self.system,
            "creator_user_id": self.creator_user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronJob:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            schedule=CronSchedule.from_dict(data.get("schedule", {})),
            message=data.get("message", ""),
            enabled=data.get("enabled", True),
            delete_after_run=data.get("delete_after_run", False),
            system=data.get("system", False),
            creator_user_id=data.get("creator_user_id", 0),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            state=CronJobState.from_dict(data.get("state", {})),
        )


@dataclass
class WorkspaceMeta:
    """Remembers topic binding for window re-creation."""

    user_id: int = 0
    thread_id: int = 0
    chat_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "chat_id": self.chat_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceMeta:
        return cls(
            user_id=data.get("user_id", 0),
            thread_id=data.get("thread_id", 0),
            chat_id=data.get("chat_id", 0),
        )


@dataclass
class CronStoreFile:
    """Per-workspace cron store (persisted to cron/jobs.json)."""

    version: int = 1
    workspace_meta: WorkspaceMeta = field(default_factory=WorkspaceMeta)
    jobs: list[CronJob] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workspace_meta": self.workspace_meta.to_dict(),
            "jobs": [j.to_dict() for j in self.jobs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronStoreFile:
        return cls(
            version=data.get("version", 1),
            workspace_meta=WorkspaceMeta.from_dict(data.get("workspace_meta", {})),
            jobs=[CronJob.from_dict(j) for j in data.get("jobs", [])],
        )
