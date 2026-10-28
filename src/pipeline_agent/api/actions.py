"""Write endpoints. Every action is role-checked server-side and audited with the user's name.

The console never changes a platform itself. Cancel / rerun / fix become events; the control
plane turns them into policy-checked, signed requests the data plane executes (the requester's
console session is the L3 approval). Human actions are allowed in dry run (a person decided),
but every one needs a reason. Prod code approval stays in GitHub (the human merge).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..boundary.capabilities import CapabilityId
from ..security.audit import AuditLogger
from ..store.db import RunRow, WorkItemRow
from .auth import Principal, require
from .views import ctx

router = APIRouter(prefix="/api/v1")


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class CancelRequest(Reason):
    platform_id: str
    pipeline_id: str
    run_id: str


class RerunRequest(Reason):
    platform_id: str
    pipeline_id: str


class ControlsRequest(Reason):
    enabled: bool | None = None
    dry_run: bool | None = None


def _actor(user: Principal) -> str:
    return f"user:{user.name} ({user.role})"


def _latest_run(request: Request, platform_id: str, pipeline_id: str, run_id: str | None = None) -> RunRow:
    c = ctx(request)
    with c.store.session() as s:
        q = select(RunRow).where(RunRow.platform_id == platform_id, RunRow.pipeline_id == pipeline_id)
        if run_id:
            q = q.where(RunRow.run_id == run_id)
        row = s.scalar(q.order_by(RunRow.last_seen.desc()).limit(1))
    if row is None:
        raise HTTPException(404, "the data plane hasn't observed this pipeline/run yet")
    return row


def _manual(request: Request, user: Principal, capability: CapabilityId, run: RunRow, target: dict, reason: str) -> dict:
    c = ctx(request)
    key = f"manual:{uuid.uuid4().hex[:12]}"
    payload = {
        "capability": capability.value, "target": target, "params": {}, "requested_by": user.name, "reason": reason,
        "pipeline_id": run.pipeline_id, "pipeline_name": run.pipeline_name, "run_id": run.run_id, "env": run.env, "url": run.url,
    }
    event_id = c.events.emit("manual_request", run.platform_id, key, payload)
    AuditLogger(c.store).log(f"{capability.value.split('.')[-1]}_requested", key, actor=_actor(user), reason=reason, action_target=target)
    return {"ok": True, "queued": True, "event_id": event_id, "note": "The control plane checks policy and the data plane executes it on their next poll."}


@router.post("/runs/cancel")
def cancel_run(body: CancelRequest, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    run = _latest_run(request, body.platform_id, body.pipeline_id, body.run_id)
    if run.status not in ("running", "queued"):
        raise HTTPException(409, "run is not active (already finished?)")
    target = {"pipeline_id": run.pipeline_id, "run_id": run.run_id}
    return _manual(request, user, CapabilityId.CANCEL, run, target, body.reason)


@router.post("/pipelines/rerun")
def rerun_pipeline(body: RerunRequest, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    run = _latest_run(request, body.platform_id, body.pipeline_id)
    return _manual(request, user, CapabilityId.RERUN, run, {"pipeline_id": run.pipeline_id}, body.reason)


@router.post("/incidents/{incident_id}/triage")
def retriage(incident_id: int, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if not c.settings.llm_enabled:
        raise HTTPException(409, "LLM triage is disabled (AGENT_LLM_ENABLED=false)")
    audit = AuditLogger(c.store)
    audit.log("retriage_requested", inc.run_key, actor=_actor(user), incident_id=incident_id)
    try:
        diag = c.triage().diagnose(
            incident_id=inc.id, platform=inc.platform_id, pipeline=inc.pipeline_name, env=inc.env, error_text=inc.error_message,
            rule_class=inc.error_class, matched_rule=inc.matched_rule,
        )
    except Exception as exc:
        raise HTTPException(502, f"no diagnosis: {exc}"[:300])
    _, tokens, cost = c.store.llm_usage(incident_id=inc.id)
    c.store.update_incident(inc.id, diagnosis=diag.model_dump(mode="json"), suspicious_content=int(diag.suspicious_content),
                            llm_tokens=tokens, llm_cost_usd=cost)
    audit.log("diagnosed", inc.run_key, actor=_actor(user), incident_id=inc.id, model=diag.model, confidence=diag.confidence)
    return {"ok": True, "diagnosis": diag.model_dump(mode="json")}


@router.post("/incidents/{incident_id}/fix")
def start_fix(incident_id: int, body: Reason, request: Request, user: Principal = Depends(require("approver"))) -> dict:
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if not (c.settings.fix_enabled and c.settings.llm_enabled):
        raise HTTPException(409, "code fixes are disabled (AGENT_FIX_ENABLED / AGENT_LLM_ENABLED)")
    if inc.status not in ("open", "escalated"):
        raise HTTPException(409, f"incident is {inc.status}")
    event_id = c.events.emit("fix_request", inc.platform_id, inc.run_key, {"incident_id": inc.id, "requested_by": user.name, "reason": body.reason})
    AuditLogger(c.store).log("fix_requested", inc.run_key, actor=_actor(user), incident_id=inc.id, reason=body.reason)
    return {"ok": True, "queued": True, "event_id": event_id, "note": "The Recovery and Testing agents start on the control plane's next poll."}


@router.post("/incidents/{incident_id}/takeover")
def take_over(incident_id: int, body: Reason, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    """A human takes the incident: its graph stops, pending approvals are rejected, any fix is marked failed."""
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    fix = c.store.fix_for_incident(incident_id)
    if fix and fix.state not in ("resolved", "failed", "rejected"):
        c.store.update_fix(fix.id, state="failed", error=f"taken over by {user.name}", note=f"taken over by {user.name}: {body.reason}")
    with c.store.session() as s:
        s.execute(update(WorkItemRow).where(WorkItemRow.incident_id == incident_id).values(status="taken_over"))
        s.execute(update(WorkItemRow).where(WorkItemRow.run_key == inc.run_key).values(status="taken_over"))
        s.commit()
    for approval in c.approvals.list(status="pending"):
        if approval.incident_id == incident_id:
            c.approvals.decide(approval.id, False, by=f"user:{user.name}", comment="incident taken over")
    c.store.update_incident(incident_id, status="escalated")
    AuditLogger(c.store).log("human_takeover", inc.run_key, actor=_actor(user), incident_id=incident_id, reason=body.reason)
    return {"ok": True}


@router.put("/controls")
def set_controls(body: ControlsRequest, request: Request, user: Principal = Depends(require("admin"))) -> dict:
    c = ctx(request)
    changed = {}
    for key in ("enabled", "dry_run"):
        value = getattr(body, key)
        if value is not None:
            c.store.set_control(key, value, actor=user.name)
            changed[key] = value
    if not changed:
        raise HTTPException(400, "nothing to change")
    AuditLogger(c.store).log("controls_changed", "agent", actor=_actor(user), reason=body.reason, **changed)
    return {"ok": True, "changed": changed, "note": "Both planes apply this on their next poll."}
