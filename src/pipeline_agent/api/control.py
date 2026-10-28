"""Console views and actions for the redesign: approvals, signed requests + ledger, KEDB, agents, policy, evidence."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from ..security.audit import AuditLogger
from ..store.db import ActionRequestRow, ActionResultRow, ApprovalRow, LedgerRow, WorkItemRow
from .actions import Reason, _actor
from .auth import Principal, current_user, require
from .context import iso
from .views import ctx

router = APIRouter(prefix="/api/v1", dependencies=[Depends(current_user)])


# ------------------------------------------------------------------- approvals
def approval_row(a: ApprovalRow) -> dict:
    return {
        "id": a.id, "created_at": iso(a.created_at), "incident_id": a.incident_id, "capability": a.capability, "summary": a.summary,
        "steps": [s.get("capability") for s in (a.proposal or {}).get("steps", [])], "reasons": (a.proposal or {}).get("reasons", []),
        "level": (a.proposal or {}).get("level"), "status": a.status, "decided_by": a.decided_by, "decided_at": iso(a.decided_at),
        "comment": a.comment,
    }


@router.get("/action-approvals")
def action_approvals(request: Request, status: str | None = Query("pending")) -> list[dict]:
    return [approval_row(a) for a in ctx(request).approvals.list(status=None if status == "all" else status)]


def _decide(approval_id: int, approve: bool, body: Reason, request: Request, user: Principal) -> dict:
    c = ctx(request)
    try:
        row = c.approvals.decide(approval_id, approve, by=f"user:{user.name}", comment=body.reason)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    AuditLogger(c.store).log("approval_granted" if approve else "approval_rejected", f"approval:{approval_id}", actor=_actor(user),
                             incident_id=row.incident_id, capability=row.capability, reason=body.reason)
    return {"ok": True, "approval": approval_row(row), "note": "The control plane resumes the incident on its next poll."}


@router.post("/action-approvals/{approval_id}/approve")
def approve(approval_id: int, body: Reason, request: Request, user: Principal = Depends(require("approver"))) -> dict:
    return _decide(approval_id, True, body, request, user)


@router.post("/action-approvals/{approval_id}/reject")
def reject(approval_id: int, body: Reason, request: Request, user: Principal = Depends(require("approver"))) -> dict:
    return _decide(approval_id, False, body, request, user)


# ------------------------------------------------------ signed requests + ledger
def request_rows(request: Request, incident_id: int | None = None, limit: int = 200) -> list[dict]:
    c = ctx(request)
    with c.store.session() as s:
        q = select(ActionRequestRow).order_by(ActionRequestRow.created_at.desc()).limit(limit)
        if incident_id is not None:
            q = q.where(ActionRequestRow.incident_id == incident_id)
        rows = s.scalars(q).all()
        ids = [r.id for r in rows]
        results = {r.request_id: r for r in s.scalars(select(ActionResultRow).where(ActionResultRow.request_id.in_(ids)).order_by(ActionResultRow.id)).all()}
        ledger = {r.request_id: r for r in s.scalars(select(LedgerRow).where(LedgerRow.request_id.in_(ids))).all()}
    out = []
    for r in rows:
        res, led, p = results.get(r.id), ledger.get(r.id), r.payload
        out.append({
            "id": r.id, "created_at": iso(r.created_at), "incident_id": r.incident_id, "agent": r.agent, "capability": r.capability,
            "platform_id": r.platform_id, "env": r.env, "level": r.level, "status": r.status, "key_id": r.key_id,
            "idempotency_key": r.idempotency_key, "target": p.get("target"), "params": {k: v for k, v in (p.get("params") or {}).items() if k != "job_parameters"},
            "approval": p.get("approval"), "reason": p.get("reason"), "policy_bundle": (p.get("policy_bundle") or "")[:12],
            "result": None if res is None else {"status": res.status, "error": res.error, "output": res.output, "evidence_ids": res.evidence_ids, "ts": iso(res.ts)},
            "ledger": None if led is None else {"state": led.state, "reserved_at": iso(led.reserved_at), "finished_at": iso(led.finished_at)},
        })
    return out


@router.get("/requests")
def requests(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
    return request_rows(request, limit=limit)


@router.get("/ledger/summary")
def ledger_summary(request: Request) -> dict:
    c = ctx(request)
    with c.store.session() as s:
        states = [row.state for row in s.scalars(select(LedgerRow)).all()]
    return {"total": len(states), **{state: states.count(state) for state in ("RESERVED", "SUCCESS", "FAILED", "UNKNOWN")}}


# ------------------------------------------------------------------------- KEDB
@router.get("/kedb")
def kedb(request: Request, status: str | None = None) -> list[dict]:
    return [e.model_dump() for e in ctx(request).kedb.list(status=status)]


@router.post("/kedb/{entry_id}/{action}")
def kedb_curate(entry_id: int, action: str, body: Reason, request: Request, user: Principal = Depends(require("admin"))) -> dict:
    if action not in ("approve", "retire"):
        raise HTTPException(404, "unknown action")
    c = ctx(request)
    try:
        entry = c.kedb.set_status(entry_id, "approved" if action == "approve" else "retired", by=f"user:{user.name}")
    except KeyError:
        raise HTTPException(404, "entry not found")
    AuditLogger(c.store).log(f"kedb_{action}d", f"kedb:{entry_id}", actor=_actor(user), entry=entry.name, reason=body.reason)
    return {"ok": True, "entry": entry.model_dump()}


# ------------------------------------------------------------- agents + policy
@router.get("/agents")
def agents(request: Request) -> list[dict]:
    reg = ctx(request).registry
    return [reg.card(name) for name in reg.names()]


@router.get("/policy")
def policy(request: Request) -> dict:
    bundle = ctx(request).policy_bundle
    return {"sha256": bundle.sha256, "version": bundle.version, "defaults": bundle.defaults, "limits": bundle.limits.model_dump(),
            "rules": [r.model_dump() for r in bundle.rules]}


# ---------------------------------------------------------------------- evidence
@router.get("/evidence/{evidence_id}")
def evidence(evidence_id: str, request: Request) -> dict:
    c = ctx(request)
    meta = c.evidence.meta(evidence_id)
    if meta is None:
        raise HTTPException(404, "evidence not found")
    try:
        text, intact = c.evidence.get_text(evidence_id), True
    except (OSError, ValueError) as exc:
        text, intact = f"(unavailable: {exc})", False
    return {"id": meta.id, "kind": meta.kind, "source": meta.source, "media_type": meta.media_type, "size": meta.size,
            "redactions": meta.redactions, "created_at": iso(meta.created_at), "intact": intact, "text": text[:200_000]}


def incident_extras(request: Request, incident_id: int, run_key: str) -> dict:
    """Evidence, signed requests, test runs and the graph's state for the incident page."""
    c = ctx(request)
    from ..store.db import EvidenceRow

    with c.store.session() as s:
        ev = s.scalars(select(EvidenceRow).where((EvidenceRow.incident_id == incident_id) | (EvidenceRow.run_key == run_key))
                       .order_by(EvidenceRow.created_at)).all()
        items = s.scalars(select(WorkItemRow).where((WorkItemRow.incident_id == incident_id) | (WorkItemRow.run_key == run_key))
                          .order_by(WorkItemRow.id)).all()
    reqs = request_rows(request, incident_id=incident_id)
    return {
        "evidence": [{"id": e.id, "kind": e.kind, "source": e.source, "size": e.size, "redactions": e.redactions, "created_at": iso(e.created_at)} for e in ev],
        "requests": reqs,
        "tests": [r for r in reqs if r["capability"] == "tests.run"],
        "work_items": [{"id": w.id, "kind": w.kind, "status": w.status, "waiting_for": ((w.details or {}).get("wait") or {}).get("wait"),
                        "outcome": (w.details or {}).get("outcome"), "reason": (w.details or {}).get("reason")} for w in items],
        "approvals": [approval_row(a) for a in c.approvals.list(status=None) if a.incident_id == incident_id],
    }
