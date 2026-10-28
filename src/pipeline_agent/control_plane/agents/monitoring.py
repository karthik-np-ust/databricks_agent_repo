"""Monitoring agent (deterministic): turns data-plane events into work items.

One work item per (kind, run): replays and repeated events are no-ops. Failures of runs the
agent itself started (a rerun that failed again) are not new problems: the incident that
started them sees the outcome through its ``run_finished`` event.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...store.db import EventRow, WorkItemRow
from ..deps import ControlPlaneDeps
from ..state import IncidentState

KINDS = {"run_failed": "failure", "run_slow": "slow", "manual_request": "manual", "fix_request": "fix"}


def _repairing(run_key: str, deps: ControlPlaneDeps) -> bool:
    with deps.store.session() as s:
        watches = s.scalars(select(EventRow).where(EventRow.kind == "watch", EventRow.run_key == run_key)).all()
    return any((w.payload or {}).get("capability") == "pipeline.repair" for w in watches)


def open_work_item(event: EventRow, deps: ControlPlaneDeps) -> tuple[int, IncidentState] | None:
    """Create the work item for ``event``; None when there is nothing new to handle."""
    kind = KINDS[event.kind]
    if kind == "failure" and deps.events.exists("watch", event.run_key):
        return None  # agent-started run: its own incident handles the outcome
    if kind == "slow" and _repairing(event.run_key, deps):
        # A repair re-runs tasks inside the original run, so its elapsed time counts from the
        # original start: never "slow". The repairing incident waits for its outcome (with a timeout).
        return None
    p = event.payload
    if kind == "fix":  # a human asked for a code fix of an incident: continue from the incident's facts
        incident = deps.store.get_incident(p["incident_id"])
        if incident is None:
            return None
        p = {
            "pipeline_id": incident.pipeline_id, "pipeline_name": incident.pipeline_name, "run_id": incident.run_key.rsplit(":", 1)[-1],
            "env": incident.env, "url": incident.url, "error_class": incident.error_class, "matched_rule": incident.matched_rule,
            "requested_by": p.get("requested_by"), "reason": p.get("reason"),
        }
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
    if kind == "manual":
        state["manual"] = {k: p.get(k) for k in ("capability", "target", "params", "requested_by", "reason")}
    if kind == "fix":
        state["hints"]["requested_by"] = p.get("requested_by")
    return work_item_id, state
