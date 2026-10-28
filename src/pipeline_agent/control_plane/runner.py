"""Graph runner: the control-plane process loop (``pipeline-agent control``). One active instance (lease).

Each tick:
  1. Monitoring: new run_failed / run_slow events -> work items -> start a graph thread each
  2. Resume waiting threads whose approval, action result or run outcome has arrived (or timed out)
  3. Retry threads that crashed mid-node from their last checkpoint (max 3 times)
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from ..leases import Leases
from ..store.db import WorkItemRow
from .agents.monitoring import KINDS, open_work_item
from .deps import ControlPlaneDeps
from .orchestrator import IncidentGraph, thread_id

log = logging.getLogger(__name__)

LEASE = "control-plane"
MAX_RETRIES = 3


@dataclass
class ControlReport:
    started_at: datetime
    leader: bool = True
    started: list[int] = field(default_factory=list)  # work item ids
    resumed: list[tuple[int, str]] = field(default_factory=list)  # (work item, what it waited for)
    waiting: list[tuple[int, str]] = field(default_factory=list)
    finished: list[tuple[int, str]] = field(default_factory=list)  # (work item, outcome)
    errors: list[str] = field(default_factory=list)


class ControlPlane:
    def __init__(
        self,
        deps: ControlPlaneDeps,
        checkpointer: Any,
        *,
        holder: str | None = None,
        lease_ttl: timedelta = timedelta(minutes=3),
        approval_timeout: timedelta = timedelta(hours=24),
        action_timeout: timedelta = timedelta(hours=2),
    ):
        self.d = deps
        self.graph = IncidentGraph(deps).build().compile(checkpointer=checkpointer)
        self.leases = Leases(deps.store)
        self.holder = holder or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_ttl = lease_ttl
        self.timeouts = {
            "approval": approval_timeout, "action_result": action_timeout, "run_finished": action_timeout, "checks": action_timeout,
            "pr_merged": timedelta(hours=deps.config.fix.max_wait_hours),
        }

    # loop ---------------------------------------------------------------------
    def tick(self) -> ControlReport:
        now = self.d.clock()
        report = ControlReport(started_at=now)
        if not self.leases.acquire(LEASE, self.holder, self.lease_ttl, now=now):
            report.leader = False
            return report
        for event in self.d.events.unconsumed(tuple(KINDS)):
            opened = open_work_item(event, self.d)
            self.d.events.consume([event.id], now=now)
            if opened:
                work_item_id, state = opened
                report.started.append(work_item_id)
                self._invoke(work_item_id, state, report)
        for item in self._items("waiting"):
            wait = (item.details or {}).get("wait") or {}
            value = self._ready(wait, now)
            if value is not None:
                report.resumed.append((item.id, wait.get("wait", "?")))
                self._invoke(item.id, Command(resume=value), report)
        for item in self._items("error"):
            if (item.details or {}).get("errors", 0) < MAX_RETRIES:
                self._invoke(item.id, None, report)  # continue from the last checkpoint
        return report

    def stop(self) -> None:
        self.leases.release(LEASE, self.holder)

    # graph threads ---------------------------------------------------------------
    def _invoke(self, work_item_id: int, graph_input: Any, report: ControlReport) -> None:
        config = {"configurable": {"thread_id": thread_id(work_item_id)}}
        try:
            out = self.graph.invoke(graph_input, config)
        except Exception as exc:
            log.exception("work item %s failed", work_item_id)
            report.errors.append(f"work item {work_item_id}: {type(exc).__name__}: {exc}")
            self._set(work_item_id, "error", errors=((self._item(work_item_id).details or {}).get("errors", 0) + 1), last_error=str(exc)[:500])
            return
        interrupts = out.get("__interrupt__") or []
        if interrupts:
            wait = {**interrupts[0].value, "since": self.d.clock().isoformat()}
            self._set(work_item_id, "waiting", wait=wait)
            report.waiting.append((work_item_id, wait["wait"]))
        else:
            report.finished.append((work_item_id, out.get("outcome", "?")))

    def _ready(self, wait: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        """The value to resume with, or None while still waiting."""
        kind = wait.get("wait")
        if kind == "approval":
            row = self.d.approvals.get(wait["approval_id"])
            if row is not None and row.status in ("approved", "rejected"):
                return {"status": row.status, "by": row.decided_by, "comment": row.comment}
        elif kind == "action_result":
            result = self.d.queue.result(wait["request_id"])
            if result is not None:
                return result.model_dump()
        elif kind == "run_finished":
            for event in self.d.events.unconsumed(("run_finished",)):
                if event.payload.get("request_id") == wait["request_id"]:
                    self.d.events.consume([event.id], now=now)
                    return {**event.payload, "evidence_ids": event.evidence_ids}
        elif kind == "checks" and self.d.repo is not None:
            pr = self.d.repo.get_pr(wait["pr"])
            status = self.d.repo.checks(pr.head_sha, self.d.config.fix.required_checks)
            if status.state in ("success", "failure"):
                return {"state": status.state, "failures": status.failures}
        elif kind == "pr_merged" and self.d.repo is not None:
            pr = self.d.repo.get_pr(wait["pr"])
            if pr.merged:
                return {"status": "merged", "merge_sha": pr.merge_sha, "url": pr.url}
            if pr.state == "closed":
                return {"status": "closed", "url": pr.url}
        since = datetime.fromisoformat(wait["since"]) if wait.get("since") else now
        if kind in self.timeouts and now - since > self.timeouts[kind]:
            return {"status": "timeout", "error": f"no {kind.replace('_', ' ')} within {self.timeouts[kind]}"}
        return None

    # work items --------------------------------------------------------------------
    def _items(self, status: str) -> list[WorkItemRow]:
        with self.d.store.session() as s:
            return list(s.scalars(select(WorkItemRow).where(WorkItemRow.status == status).order_by(WorkItemRow.id)).all())

    def _item(self, work_item_id: int) -> WorkItemRow:
        with self.d.store.session() as s:
            return s.get(WorkItemRow, work_item_id)

    def _set(self, work_item_id: int, status: str, **details: Any) -> None:
        with self.d.store.session() as s:
            row = s.get(WorkItemRow, work_item_id)
            if row.status in ("done", "escalated", "waiting_fix") and status == "waiting":
                return  # the graph closed it in the same invoke
            row.status, row.updated_at = status, self.d.clock()
            row.details = {**(row.details or {}), **details}
            s.commit()
