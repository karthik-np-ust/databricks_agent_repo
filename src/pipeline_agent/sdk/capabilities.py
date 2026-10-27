"""Small capability interfaces. A plugin implements only what its platform supports.

The policy engine, monitor and UI check capabilities before acting, e.g. a
platform without CANCEL gets an escalation instead of cancel-and-rerun.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .models import CodeRef, CostRecord, FailureContext, Pipeline, Run, RunEvent, RunRef


class Capability(StrEnum):
    MONITOR = "monitor"
    CANCEL = "cancel"
    RERUN = "rerun"
    LOGS = "logs"
    COST = "cost"
    CODE = "code"
    EVENTS = "events"
    ACTIONS = "actions"  # data-plane actions (redesign): repair, code sync, tests, profiles


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
class ActionProvider(Protocol):
    """ACTIONS: what the data plane's capability router needs beyond cancel/rerun.

    Long-running work (repair, tests) returns as soon as the platform accepted it; the data
    plane's observer watches the run and reports the outcome.
    """

    def get_run(self, run_id: str) -> Run: ...

    def repair(self, run: Run) -> dict[str, Any]:
        """Re-run only the failed tasks of ``run``. Returns at least ``run_id``."""
        ...

    def sync_code(self, env: str) -> dict[str, Any]:
        """Update the env's checked-out code (e.g. a Git folder) to its branch head. Returns ``head``."""
        ...

    def run_tests(self, branch: str, notebooks: list[str], params: dict[str, str]) -> dict[str, Any]:
        """One-time run of test notebooks from ``branch``. Returns at least ``run_id``."""
        ...

    def profile(self, table: str, keys: list[str], not_null: list[str]) -> dict[str, Any]:
        """Read-only: ``rows``, ``duplicate_keys`` and ``nulls`` per column."""
        ...

    def definition_hash(self, pipeline_id: str) -> str:
        """Fingerprint of the pipeline's definition, to detect drift between decision and execution."""
        ...


@runtime_checkable
class EventSource(Protocol):
    """EVENTS: optional push (webhooks) instead of polling."""

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[RunEvent]: ...
