"""Monitoring agent (deterministic): turns data-plane events into work items.

One work item per (kind, run): replays and repeated events are no-ops. Failures of runs the
agent itself started (a rerun that failed again) are not new problems: the incident that
started them sees the outcome through its ``run_finished`` event.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from ...store.db import EventRow, WorkItemRow
from ..deps import ControlPlaneDeps
from ..state import IncidentState

KINDS = {"run_failed": "failure", "run_slow": "slow"}


def open_work_item(event: EventRow, deps: ControlPlaneDeps) -> tuple[int, IncidentState] | None:
    """Create the work item for ``event``; None when there is nothing new to handle."""
    kind = KINDS[event.kind]
    if kind == "failure" and deps.events.exists("watch", event.run_key):
        return None  # agent-started run: its own incident handles the outcome
    p = event.payload
    now = deps.clock()
    try:
        with deps.store.session() as s:
            row = WorkItemRow(
                created_at=now, updated_at=now, kind=kind, run_key=event.run_key, platform_id=event.platform_id,
                pipeline_name=p["pipeline_name"], env=p["env"], status="open", details={"event_id": event.id},
            )
            s.add(row)
            s.flush()
            row.thread_id = f"wi-{row.id}"
            s.commit()
            work_item_id = row.id
    except IntegrityError:
        return None  # already a work item for this run
    state: IncidentState = {
        "work_item_id": work_item_id, "kind": kind, "platform_id": event.platform_id, "pipeline_id": p["pipeline_id"],
        "pipeline_name": p["pipeline_name"], "run_id": p["run_id"], "run_key": event.run_key, "env": p["env"], "url": p.get("url"),
        "parameters": p.get("parameters") or {}, "evidence_ids": list(event.evidence_ids or []),
        "hints": {k: p.get(k) for k in ("error_class", "matched_rule", "failed_tasks", "error_headline", "elapsed_seconds", "threshold_seconds")},
        "step": 0, "results": [], "approval": None,
    }
    return work_item_id, state
