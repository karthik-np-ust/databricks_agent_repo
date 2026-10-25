"""Canonical, platform-neutral data model.

Every plugin maps its native objects into these types. The core (policy,
monitor, workflow, UI) only ever sees these models. The original platform
payload is kept in ``raw`` so no information is lost.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Env(StrEnum):
    DEV = "dev"
    QA = "qa"
    PROD = "prod"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.SKIPPED}

    @property
    def is_active(self) -> bool:
        return self in {RunStatus.QUEUED, RunStatus.RUNNING}


class ErrorClass(StrEnum):
    """Failure taxonomy that decides which path the agent takes."""

    TRANSIENT = "transient"  # rerun, no code change
    CODE = "code"  # code / data-handling bug -> fix flow
    INFRA = "infra"  # permission, secret, quota, config -> escalate only
    UNKNOWN = "unknown"  # escalate with diagnosis


class Pipeline(BaseModel):
    platform_id: str
    pipeline_id: str
    name: str
    env: Env = Env.PROD
    url: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return f"{self.platform_id}:{self.pipeline_id}"


class Run(BaseModel):
    platform_id: str
    pipeline_id: str
    pipeline_name: str
    run_id: str
    status: RunStatus
    env: Env = Env.PROD
    start_time: datetime | None = None
    end_time: datetime | None = None
    url: str | None = None
    triggered_by: str | None = None
    parameters: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return f"{self.platform_id}:{self.pipeline_id}:{self.run_id}"

    def elapsed(self, now: datetime | None = None) -> timedelta | None:
        if self.start_time is None:
            return None
        end = self.end_time or now or utcnow()
        return end - self.start_time


class RunRef(BaseModel):
    platform_id: str
    pipeline_id: str
    run_id: str
    url: str | None = None


class TaskFailure(BaseModel):
    task_key: str
    error_message: str = ""
    error_trace: str | None = None
    logs: str | None = None


class FailureContext(BaseModel):
    run: Run
    error_message: str
    error_class: ErrorClass = ErrorClass.UNKNOWN
    matched_rule: str | None = None
    tasks: list[TaskFailure] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    def full_text(self) -> str:
        """All error text, used for classification and (after redaction) for the LLM."""
        parts = [self.error_message]
        for t in self.tasks:
            parts += [t.error_message, t.error_trace or "", t.logs or ""]
        return "\n".join(p for p in parts if p)


class CostRecord(BaseModel):
    platform_id: str
    usage_date: date
    quantity: float
    unit: str  # e.g. "DBU", "credits"
    pipeline_id: str | None = None
    run_id: str | None = None
    env: Env | None = None
    estimated_cost_usd: float | None = None
    is_estimate: bool = False


class CodeRef(BaseModel):
    repo: str
    path: str
    ref: str = "main"
    deploy_target: str | None = None


class RunEvent(BaseModel):
    event_type: str
    run: Run
