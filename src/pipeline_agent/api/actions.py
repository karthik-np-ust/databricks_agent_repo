"""Write endpoints. Every action is role-checked server-side and audited with the user's name.

Human actions are allowed in dry run (a person decided), but every one needs a reason.
The PR approval itself is deliberately NOT here: it happens in GitHub, which enforces
reviews and records who approved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..sdk.capabilities import Capability
from ..sdk.models import Pipeline, utcnow
from ..security.audit import AuditLogger
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


def _instance(request: Request, platform_id: str, cap: Capability):
    inst = ctx(request).instances.get(platform_id)
    if inst is None:
        raise HTTPException(404, f"unknown platform {platform_id}")
    if not inst.supports(cap):
        raise HTTPException(409, f"{platform_id} does not support {cap.value}")
    return inst


@router.post("/runs/cancel")
def cancel_run(body: CancelRequest, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    inst = _instance(request, body.platform_id, Capability.CANCEL)
    run = next((r for r in inst.run_source.list_runs(since=utcnow(), active_only=True) if r.run_id == body.run_id), None)
    if run is None:
        raise HTTPException(404, "run is not active (already finished?)")
    inst.controller.cancel(run)
    AuditLogger(ctx(request).store).log("cancel", run.key, actor=_actor(user), reason=body.reason)
    ctx(request).cache.clear()
    return {"ok": True}


@router.post("/pipelines/rerun")
def rerun_pipeline(body: RerunRequest, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    inst = _instance(request, body.platform_id, Capability.RERUN)
    pipeline = next((p for p in inst.run_source.list_pipelines() if p.pipeline_id == body.pipeline_id), None)
    if pipeline is None:
        raise HTTPException(404, "pipeline not found")
    ref = inst.controller.rerun(pipeline)
    AuditLogger(ctx(request).store).log("rerun", f"{body.platform_id}:{body.pipeline_id}", actor=_actor(user),
                                        reason=body.reason, new_run_id=ref.run_id)
    ctx(request).cache.clear()
    return {"ok": True, "run_id": ref.run_id}


@router.post("/incidents/{incident_id}/triage")
def retriage(incident_id: int, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if not c.settings.llm_enabled:
        raise HTTPException(409, "LLM triage is disabled (AGENT_LLM_ENABLED=false)")
    AuditLogger(c.store).log("retriage_requested", f"incident:{incident_id}", actor=_actor(user))
    diag = c.monitor().run_triage(incident_id, inc.error_message)
    if diag is None:
        raise HTTPException(502, "no diagnosis (LLM unavailable or budget reached; see audit log)")
    return {"ok": True, "diagnosis": diag.model_dump(mode="json")}


@router.post("/incidents/{incident_id}/fix")
def start_fix(incident_id: int, body: Reason, request: Request, user: Principal = Depends(require("approver"))) -> dict:
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if not (c.settings.fix_enabled and c.settings.llm_enabled):
        raise HTTPException(409, "fix flow is disabled (AGENT_FIX_ENABLED / AGENT_LLM_ENABLED)")
    fixer = c.monitor().fixer
    fix = fixer.start(incident_id)
    if fix is None:
        raise HTTPException(409, "another fix is already in flight (fix.max_concurrent)")
    AuditLogger(c.store).log("fix_requested", f"incident:{incident_id}", actor=_actor(user), reason=body.reason, fix_id=fix.id)
    return {"ok": True, "fix_id": fix.id, "state": fix.state,
            "note": "The running agent advances the fix on its next poll."}


@router.post("/incidents/{incident_id}/takeover")
def take_over(incident_id: int, body: Reason, request: Request, user: Principal = Depends(require("operator"))) -> dict:
    """A human takes the incident: the agent stops working on it (any active fix is marked failed)."""
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    fix = c.store.fix_for_incident(incident_id)
    if fix and fix.state in ("planned", "pr_open", "deploying", "verifying"):
        c.store.update_fix(fix.id, state="failed", error=f"taken over by {user.name}", note=f"taken over by {user.name}: {body.reason}")
    c.store.update_incident(incident_id, status="escalated")
    AuditLogger(c.store).log("human_takeover", f"incident:{incident_id}", actor=_actor(user), reason=body.reason)
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
    return {"ok": True, "changed": changed, "note": "The agent applies this on its next poll."}
