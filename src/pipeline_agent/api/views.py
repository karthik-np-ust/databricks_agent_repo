"""Read endpoints for the Ops Console (role: viewer and above)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta, timezone
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import __version__
from ..sdk.models import Run, utcnow
from ..sdk.registry import discover_plugins
from ..store.db import FixAttemptRow, IncidentRow, RunRow
from .auth import Principal, current_user, require
from .context import ConsoleContext, iso

log = logging.getLogger(__name__)
ACTIVE_FIX_STATES = ("planned", "pr_open", "deploying", "verifying")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require("viewer"))])


def ctx(request: Request) -> ConsoleContext:
    return request.app.state.ctx


def _duration(start: datetime | None, end: datetime | None) -> float | None:
    return (end - start).total_seconds() if start and end else None


def _run_row(r: RunRow) -> dict:
    return {
        "key": r.key, "platform_id": r.platform_id, "pipeline_id": r.pipeline_id, "pipeline_name": r.pipeline_name,
        "run_id": r.run_id, "env": r.env, "status": r.status, "start_time": iso(r.start_time), "end_time": iso(r.end_time),
        "duration_s": _duration(r.start_time, r.end_time), "triggered_by": r.triggered_by, "url": r.url,
    }


def _incident(i: IncidentRow, fix: FixAttemptRow | None = None) -> dict:
    d = i.diagnosis or {}
    return {
        "id": i.id, "status": i.status, "error_class": i.error_class, "matched_rule": i.matched_rule,
        "platform_id": i.platform_id, "pipeline_id": i.pipeline_id, "pipeline_name": i.pipeline_name, "env": i.env,
        "run_key": i.run_key, "url": i.url, "created_at": iso(i.created_at), "updated_at": iso(i.updated_at),
        "fix_eligible": bool(i.fix_eligible), "suspicious_content": bool(i.suspicious_content),
        "llm_tokens": i.llm_tokens, "llm_cost_usd": i.llm_cost_usd,
        "summary": d.get("summary"), "confidence": d.get("confidence"), "fix_state": fix.state if fix else None,
        "headline": next((line for line in (i.error_message or "").splitlines() if line.strip()), ""),
    }


def _fix(f: FixAttemptRow, promotion: list[str]) -> dict:
    target = promotion[min(f.stage, len(promotion) - 1)]
    notes = [h.get("note", "") for h in f.history or []]
    return {
        "id": f.id, "incident_id": f.incident_id, "state": f.state, "stage": f.stage, "target": target,
        "promotion": promotion, "branch": f.branch, "commit_sha": f.commit_sha, "pr_number": f.pr_number,
        "prs": f.prs or {}, "proposal": f.proposal, "error": f.error, "history": f.history or [],
        "created_at": iso(f.created_at), "updated_at": iso(f.updated_at),
        "awaiting_human": f.state == "pr_open" and (
            (f.stage == len(promotion) - 1 and "awaiting approval" in notes) or (notes and notes[-1] == "merge blocked")
        ),
        "repairs": sum(1 for n in notes if n.startswith("repair attempt")),
    }


def _start_of_day(now: datetime) -> datetime:
    return datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)


# The console never calls a platform: everything below comes from the observer's snapshots
# (data plane), so this process needs no platform credentials.
def _pipelines_live(c: ConsoleContext) -> list[dict]:
    return [p for data, _ in c.store.snapshots("pipelines").values() for p in data or []]


def _active_runs(c: ConsoleContext) -> list[Run]:
    return [Run.model_validate(r) for data, _ in c.store.snapshots("active_runs").values() for r in data or []]


def _platforms(c: ConsoleContext) -> list[dict]:
    return [{"id": iid, **data, "last_poll": iso(updated)} for iid, (data, updated) in sorted(c.store.snapshots("platform").items())]


# --------------------------------------------------------------------------- me
@router.get("/me")
def me(user: Principal = Depends(current_user)) -> dict:
    return {"name": user.name, "role": user.role}


# --------------------------------------------------------------------- overview
@router.get("/overview")
def overview(request: Request) -> dict:
    c = ctx(request)
    now = utcnow()
    runs = c.store.list_runs(since=now - timedelta(hours=24), limit=2000)
    incidents = c.store.list_incidents()
    fixes = c.store.list_fixes()
    promotion = c.config.fix.promotion
    controls = c.store.get_controls()
    calls, tokens, cost = c.store.llm_usage(since=_start_of_day(now))
    resolved = [i for i in incidents if i.status == "resolved"]
    mttr = mean((i.updated_at - i.created_at).total_seconds() for i in resolved) if resolved else None
    fixed_by_agent = {f.incident_id for f in fixes if f.state == "resolved"}
    last_tick = controls.get("last_tick")
    pipelines = _pipelines_live(c)
    fixes_out = [_fix(f, promotion) for f in fixes]
    return {
        "version": __version__,
        "agent": {
            "enabled": c.settings.enabled and controls.get("enabled", True) is not False,
            "dry_run": controls.get("dry_run", c.settings.dry_run),
            "llm_enabled": c.settings.llm_enabled, "fix_enabled": c.settings.fix_enabled,
            "last_tick": last_tick,
            "poll_interval_s": c.settings.poll_interval_seconds,
            "platforms": [{k: p[k] for k in ("id", "plugin", "display_name", "capabilities", "last_poll")} for p in _platforms(c)],
        },
        "kpis": {
            "pipelines": len(pipelines),
            "pipelines_by_env": {e: sum(1 for p in pipelines if p["env"] == e) for e in ("prod", "qa", "dev")},
            "runs_24h": len(runs),
            "runs_24h_failed": sum(1 for r in runs if r.status == "failed"),
            "runs_24h_succeeded": sum(1 for r in runs if r.status == "succeeded"),
            "running_now": len(_active_runs(c)),
            "incidents_open": sum(1 for i in incidents if i.status in ("open", "fixing")),
            "incidents_escalated": sum(1 for i in incidents if i.status == "escalated"),
            "incidents_resolved": len(resolved),
            "incidents_total": len(incidents),
            "auto_resolved_pct": round(100 * len(fixed_by_agent) / len(incidents)) if incidents else None,
            "mttr_s": mttr,
            "fixes_active": sum(1 for f in fixes if f.state in ACTIVE_FIX_STATES),
            "awaiting_human": sum(1 for f in fixes_out if f["awaiting_human"]),
            "approvals_pending": len(c.approvals.list(status="pending")),
            "llm_calls_today": calls, "llm_tokens_today": tokens, "llm_cost_today": cost,
            "llm_requests_budget": c.config.budgets.per_day_requests, "llm_cost_budget": c.config.budgets.per_day_usd,
        },
        "attention": [f for f in fixes_out if f["awaiting_human"]][:5],
        "recent_incidents": [_incident(i, c.store.fix_for_incident(i.id)) for i in incidents[:5]],
        "activity": [
            {"ts": iso(a.ts), "actor": a.actor, "action": a.action, "target": a.target, "details": a.details}
            for a in c.store.list_audit(15)
        ],
    }


# -------------------------------------------------------------------- platforms
@router.get("/platforms")
def platforms(request: Request) -> list[dict]:
    return _platforms(ctx(request))


# -------------------------------------------------------------------- pipelines
@router.get("/pipelines")
def pipelines(request: Request) -> list[dict]:
    c = ctx(request)
    runs = c.store.list_runs(since=utcnow() - timedelta(days=14), limit=5000)
    by_pipeline: dict[tuple[str, str], list[RunRow]] = defaultdict(list)
    for r in runs:
        by_pipeline[(r.platform_id, r.pipeline_id)].append(r)
    open_incidents = defaultdict(int)
    for i in c.store.list_incidents():
        if i.status in ("open", "fixing", "escalated"):
            open_incidents[(i.platform_id, i.pipeline_id)] += 1
    out = []
    for p in _pipelines_live(c):
        rs = by_pipeline.get((p["platform_id"], p["pipeline_id"]), [])
        terminal = [r for r in rs if r.status in ("succeeded", "failed")]
        durations = [d for r in terminal if r.status == "succeeded" and (d := _duration(r.start_time, r.end_time))]
        policy = c.config.policy_for(p["platform_id"], p["name"])
        out.append({
            **p,
            "last_status": rs[0].status if rs else None,
            "last_run_at": iso(rs[0].start_time) if rs else None,
            "runs_14d": len(rs),
            "success_rate": round(100 * sum(1 for r in terminal if r.status == "succeeded") / len(terminal)) if terminal else None,
            "avg_duration_s": round(mean(durations)) if durations else None,
            "threshold_s": policy.max_duration_seconds,
            "auto_fix": policy.auto_fix,
            "acts_in_env": p["env"] in [e.value for e in policy.act_in_envs],
            "open_incidents": open_incidents[(p["platform_id"], p["pipeline_id"])],
        })
    order = {"prod": 0, "qa": 1, "dev": 2}
    return sorted(out, key=lambda p: (order.get(p["env"], 9), p["name"]))


@router.get("/pipelines/{platform_id}/{pipeline_id}")
def pipeline_detail(platform_id: str, pipeline_id: str, request: Request) -> dict:
    c = ctx(request)
    p = next((p for p in pipelines(request) if p["platform_id"] == platform_id and p["pipeline_id"] == pipeline_id), None)
    if p is None:
        raise HTTPException(404, "pipeline not found")
    policy = c.config.policy_for(platform_id, p["name"])
    return {
        "pipeline": p,
        "policy": policy.model_dump(mode="json"),
        "runs": [_run_row(r) for r in c.store.list_runs(limit=100, pipeline_id=pipeline_id) if r.platform_id == platform_id],
        "incidents": [_incident(i) for i in c.store.list_incidents() if i.platform_id == platform_id and i.pipeline_id == pipeline_id],
    }


# ------------------------------------------------------------------------- runs
@router.get("/runs")
def runs(request: Request, hours: int = Query(24, ge=1, le=24 * 30), status: str | None = None, env: str | None = None) -> list[dict]:
    rows = ctx(request).store.list_runs(since=utcnow() - timedelta(hours=hours), limit=2000)
    return [_run_row(r) for r in rows if (not status or r.status == status) and (not env or r.env == env)]


@router.get("/runs/active")
def runs_active(request: Request) -> list[dict]:
    c = ctx(request)
    now = utcnow()
    caps = {p["id"]: set(p["capabilities"]) for p in _platforms(c)}
    out = []
    for r in _active_runs(c):
        policy = c.config.policy_for(r.platform_id, r.pipeline_name)
        elapsed = r.elapsed(now)
        threshold = policy.max_duration_seconds
        out.append({
            "platform_id": r.platform_id, "pipeline_id": r.pipeline_id, "pipeline_name": r.pipeline_name,
            "run_id": r.run_id, "env": r.env.value, "status": r.status.value, "start_time": iso(r.start_time),
            "elapsed_s": elapsed.total_seconds() if elapsed else None, "threshold_s": threshold,
            "pct_of_threshold": round(100 * elapsed.total_seconds() / threshold) if elapsed and threshold else None,
            "url": r.url, "parameters": r.parameters,
            "can_cancel": "cancel" in caps.get(r.platform_id, set()),
        })
    return sorted(out, key=lambda r: -(r["pct_of_threshold"] or 0))


# -------------------------------------------------------------------- incidents
@router.get("/incidents")
def incidents(request: Request, status: str | None = None) -> list[dict]:
    c = ctx(request)
    return [_incident(i, c.store.fix_for_incident(i.id)) for i in c.store.list_incidents(status)]


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: int, request: Request) -> dict:
    c = ctx(request)
    inc = c.store.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    fix = c.store.fix_for_incident(incident_id)
    targets = [inc.run_key, f"incident:{incident_id}"] + ([f"fix:{fix.id}"] if fix else [])
    from .control import incident_extras  # late import: control.py imports this module

    return {
        **incident_extras(request, incident_id, inc.run_key),
        "incident": _incident(inc, fix),
        "error_text": inc.error_message,
        "diagnosis": inc.diagnosis,
        "fix": _fix(fix, c.config.fix.promotion) if fix else None,
        "audit": [{"ts": iso(a.ts), "actor": a.actor, "action": a.action, "target": a.target, "details": a.details}
                  for a in c.store.audit_for_targets(targets)],
        "llm_calls": [{"ts": iso(l.ts), "purpose": l.purpose, "model": l.model, "prompt_tokens": l.prompt_tokens,
                       "completion_tokens": l.completion_tokens, "cost_usd": l.cost_usd, "latency_ms": l.latency_ms,
                       "success": bool(l.success), "error": l.error}
                      for l in c.store.llm_calls_for_incident(incident_id)],
    }


# ------------------------------------------------------------------------ fixes
@router.get("/fixes")
def fixes(request: Request) -> list[dict]:
    c = ctx(request)
    out = []
    for f in reversed(c.store.list_fixes()):
        inc = c.store.get_incident(f.incident_id)
        out.append({**_fix(f, c.config.fix.promotion), "pipeline_name": inc.pipeline_name if inc else None})
    return out


def _pr_number(url: str | None) -> int | None:
    try:
        return int(str(url).rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, TypeError):
        return None


@router.get("/fixes/{fix_id}/diff")
def fix_diff(fix_id: int, request: Request) -> dict:
    c = ctx(request)
    fix = c.store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(404, "fix not found")
    first = (fix.prs or {}).get(c.config.fix.promotion[0])
    number = _pr_number(first)
    gh = c.github
    if gh is None or number is None:
        return {"available": False, "reason": "GitHub not configured or PR not opened yet", "files": [], "comments": []}
    try:
        files = c.cache.get(f"diff:{number}:{fix.commit_sha}", 60, lambda: gh.pr_files(number))
        comments = c.cache.get(f"comments:{number}:{fix.commit_sha}", 60, lambda: gh.pr_comments(number))
    except Exception as exc:
        return {"available": False, "reason": f"GitHub error: {exc}"[:200], "files": [], "comments": []}
    return {"available": True, "pr_number": number, "pr_url": first, "files": files, "comments": comments}


@router.get("/approvals")
def approvals(request: Request) -> list[dict]:
    return [f for f in fixes(request) if f["awaiting_human"]]


# ------------------------------------------------------------------------ costs
@router.get("/costs/compute")
def costs_compute(request: Request, days: int = Query(14, ge=1, le=90)) -> dict:
    c = ctx(request)
    since = utcnow() - timedelta(days=days)
    names = {(p["platform_id"], p["pipeline_id"]): (p["name"], p["env"]) for p in _pipelines_live(c)}

    records, errors, estimate = [], [], False
    for iid, (data, _) in c.store.snapshots("costs").items():  # refreshed by the observer every 30 min
        if data.get("error"):
            errors.append(f"{iid}: {data['error']}")
        for rec in data.get("records", []):
            if rec["usage_date"] < since.date().isoformat():
                continue
            name, env = names.get((iid, rec.get("pipeline_id") or ""), (rec.get("pipeline_id") or "other", rec.get("env")))
            estimate = estimate or rec.get("is_estimate", False)
            records.append({"date": rec["usage_date"], "platform_id": iid, "pipeline": name, "env": env or rec.get("env") or "unknown",
                            "quantity": rec["quantity"], "unit": rec["unit"], "cost_usd": rec.get("estimated_cost_usd")})
    by_day_env: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_pipeline: dict[str, float] = defaultdict(float)
    for r in records:
        by_day_env[r["date"]][r["env"]] += r["quantity"]
        by_pipeline[f'{r["pipeline"]} ({r["env"]})'] += r["quantity"]
    return {
        "unit": records[0]["unit"] if records else "DBU",
        "is_estimate": estimate,
        "total": round(sum(r["quantity"] for r in records), 4),
        "by_day": [{"date": d, **{e: round(v, 4) for e, v in envs.items()}} for d, envs in sorted(by_day_env.items())],
        "by_pipeline": sorted(({"pipeline": k, "quantity": round(v, 4)} for k, v in by_pipeline.items()), key=lambda x: -x["quantity"]),
        "errors": errors,
    }


@router.get("/costs/llm")
def costs_llm(request: Request, days: int = Query(14, ge=1, le=90)) -> dict:
    c = ctx(request)
    calls = c.store.list_llm_calls_since(utcnow() - timedelta(days=days))
    by_day: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0, "failed": 0})
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0, "failed": 0, "latency_ms": []})
    by_purpose: dict[str, int] = defaultdict(int)
    for l in calls:
        tok = l.prompt_tokens + l.completion_tokens
        for bucket in (by_day[l.ts.date().isoformat()], by_model[l.model]):
            bucket["calls"] += 1
            bucket["tokens"] += tok
            bucket["cost_usd"] += l.cost_usd
            bucket["failed"] += 0 if l.success else 1
        if l.success:
            by_model[l.model]["latency_ms"].append(l.latency_ms)
        by_purpose[l.purpose] += tok
    b = c.config.budgets
    today = c.store.llm_usage(since=_start_of_day(utcnow()))
    return {
        "budgets": b.model_dump(),
        "today": {"calls": today[0], "tokens": today[1], "cost_usd": today[2]},
        "by_day": [{"date": d, **v} for d, v in sorted(by_day.items())],
        "by_model": sorted(
            ({"model": m, **{k: v for k, v in s.items() if k != "latency_ms"},
              "avg_latency_ms": round(mean(s["latency_ms"])) if s["latency_ms"] else None} for m, s in by_model.items()),
            key=lambda x: -x["calls"]),
        "by_purpose": [{"purpose": p, "tokens": t} for p, t in sorted(by_purpose.items(), key=lambda x: -x[1])],
        "per_incident": [{"incident_id": i.id, "pipeline": i.pipeline_name, "tokens": i.llm_tokens, "cost_usd": i.llm_cost_usd}
                         for i in c.store.list_incidents() if i.llm_tokens],
    }


@router.get("/llm/calls")
def llm_calls(request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return [{"id": l.id, "ts": iso(l.ts), "incident_id": l.incident_id, "purpose": l.purpose, "model": l.model,
             "prompt_tokens": l.prompt_tokens, "completion_tokens": l.completion_tokens, "cost_usd": l.cost_usd,
             "latency_ms": l.latency_ms, "redactions": l.redactions, "success": bool(l.success), "error": l.error}
            for l in ctx(request).store.list_llm_calls(limit)]


# ------------------------------------------------------------------------ audit
@router.get("/audit")
def audit(request: Request, actor: str | None = None, action: str | None = None, q: str | None = None,
          limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    return [{"id": a.id, "ts": iso(a.ts), "actor": a.actor, "action": a.action, "target": a.target, "details": a.details}
            for a in ctx(request).store.query_audit(actor, action, q, limit)]


# --------------------------------------------------------------------- settings
@router.get("/settings")
def settings_view(request: Request) -> dict:
    c = ctx(request)
    gh = c.github
    identity = None
    if gh is not None:
        try:
            identity = c.cache.get("gh_identity", 600, gh.whoami)
        except Exception as exc:
            identity = f"error: {exc}"[:120]
    return {
        "controls": [{"key": r.key, "value": r.value, "updated_at": iso(r.updated_at), "updated_by": r.updated_by}
                     for r in c.store.list_controls()],
        "env_settings": {"enabled": c.settings.enabled, "dry_run": c.settings.dry_run, "llm_enabled": c.settings.llm_enabled,
                         "fix_enabled": c.settings.fix_enabled, "poll_interval_s": c.settings.poll_interval_seconds,
                         "notify_kind": c.settings.notify_kind, "console_auth": c.settings.console_auth},
        "platforms": [
            {"id": pc.id, "plugin": pc.plugin, "enabled": pc.enabled, "config": pc.config,
             "secrets": {k: v.split("://")[0] + "://…" for k, v in pc.secrets.items()}}  # references only, never values
            for pc in c.config.platforms
        ],
        "plugins": [{"name": n, "version": cls.manifest.version, "display_name": cls.manifest.display_name,
                     "capabilities": sorted(cap.value for cap in cls.manifest.capabilities),
                     "config_schema": cls.manifest.config_schema}
                    for n, cls in sorted(discover_plugins().items())],
        "defaults": c.config.defaults.model_dump(mode="json"),
        "policies": [p.model_dump(mode="json", exclude_unset=True) for p in c.config.policies],
        "circuit_breaker": c.config.circuit_breaker.model_dump(),
        "budgets": c.config.budgets.model_dump(),
        "llm": c.config.llm.model_dump(),
        "fix": c.config.fix.model_dump(),
        "github_identity": identity,
    }
