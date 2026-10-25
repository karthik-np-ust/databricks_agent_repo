"""Small capability interfaces. A plugin implements only what its platform supports.

The policy engine, monitor and UI check capabilities before acting, e.g. a
platform without CANCEL gets an escalation instead of cancel-and-rerun.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .models import CodeRef, CostRecord, FailureContext, Pipeline, Run, RunEvent, RunRef


class Capability(StrEnum):
    MONITOR = "monitor"
    CANCEL = "cancel"
    RERUN = "rerun"
    LOGS = "logs"
    COST = "cost"
    CODE = "code"
    EVENTS = "events"


@runtime_checkable
class RunSource(Protocol):
    """MONITOR (required for every plugin)."""

    def list_pipelines(self) -> list[Pipeline]: ...

    def list_runs(self, since: datetime, active_only: bool = False) -> list[Run]:
        """Runs that are active now, plus runs that ended at or after ``since``."""
        ...


@runtime_checkable
class RunController(Protocol):
    """CANCEL and/or RERUN."""

    def cancel(self, run: Run) -> None: ...

    def rerun(self, pipeline: Pipeline, params: dict[str, str] | None = None) -> RunRef: ...


@runtime_checkable
class LogProvider(Protocol):
    """LOGS: error text, traces and a canonical ErrorClass for a failed run."""

    def failure_context(self, run: Run) -> FailureContext: ...


@runtime_checkable
class CostProvider(Protocol):
    """COST: compute usage per pipeline/run."""

    def costs(self, since: datetime) -> list[CostRecord]: ...


@runtime_checkable
class CodeLocator(Protocol):
    """CODE: where the pipeline's source lives, so the agent can debug and fix it."""

    def code_ref(self, pipeline: Pipeline) -> CodeRef | None: ...


@runtime_checkable
class EventSource(Protocol):
    """EVENTS: optional push (webhooks) instead of polling."""

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[RunEvent]: ...
