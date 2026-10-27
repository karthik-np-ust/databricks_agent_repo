"""Execution service: the only code that changes platform state.

For each claimed request:
  1. verify signature + expiry               (refuse anything unsigned, tampered or stale)
  2. policy bundle hash == ours              (refuse decisions made under another policy)
  3. re-evaluate policy locally              (caps, kill switch, approval still valid)
  4. preconditions against live state        (run still failed, no newer success, no drift)
  5. reserve the ledger key                  (a replay gets the recorded outcome instead)
  6. execute -> post-conditions -> finish ledger -> evidence -> result
Runs it started are watched by the observer, which reports their outcome as events.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from ..autonomy import PolicyEngine, PolicyInput
from ..boundary.capabilities import CapabilityId
from ..boundary.events import EventLog
from ..boundary.models import ActionRequest, ActionResult, BoundaryError, SignedActionRequest
from ..boundary.queue import ActionQueue
from ..evidence import EvidenceStore
from ..sdk.models import utcnow
from ..security.audit import AuditLogger
from ..security.redact import redact
from ..security.signing import Verifier
from ..store.db import StateStore
from .ledger import ExecutionLedger
from .router import CapabilityRouter, PreconditionFailed, RouteError

log = logging.getLogger(__name__)

ACTOR = "data-plane"
RATE_LIMITED = (CapabilityId.RERUN.value, CapabilityId.REPAIR.value, CapabilityId.CANCEL.value)
WATCHED = {CapabilityId.RERUN, CapabilityId.REPAIR, CapabilityId.SMOKE_RUN, CapabilityId.TESTS_RUN}


class Executor:
    def __init__(
        self,
        store: StateStore,
        router: CapabilityRouter,
        verifier: Verifier,
        engine: PolicyEngine,
        evidence: EvidenceStore,
        *,
        worker: str = "data-plane",
        controls: Callable[[], dict[str, Any]] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.store = store
        self.queue = ActionQueue(store)
        self.ledger = ExecutionLedger(store)
        self.events = EventLog(store)
        self.audit = AuditLogger(store)
        self.router = router
        self.verifier = verifier
        self.engine = engine
        self.evidence = evidence
        self.worker = worker
        self.controls = controls or (lambda: {"enabled": True, "dry_run": False})
        self.clock = clock

    def drain(self, limit: int = 20) -> list[ActionResult]:
        results = []
        for _ in range(limit):
            result = self.process_one()
            if result is None:
                break
            results.append(result)
        return results

    def process_one(self) -> ActionResult | None:
        signed = self.queue.claim_next(self.worker, now=self.clock())
        if signed is None:
            return None
        result = self._process(signed)
        self.queue.complete(result, now=self.clock())
        self.audit.log(
            f"action_{result.status}", f"request:{result.request_id}", actor=ACTOR,
            capability=signed.payload.get("capability"), idempotency_key=signed.payload.get("idempotency_key"),
            error=result.error, output=result.output,
        )
        return result

    def _reject(self, request_id: str, reason: str, **output: Any) -> ActionResult:
        log.warning("rejected request %s: %s", request_id, reason)
        return ActionResult(request_id=request_id, status="rejected", error=reason, output=output)

    def _process(self, signed: SignedActionRequest) -> ActionResult:
        request_id = str(signed.payload.get("id", "?"))
        now = self.clock()
        try:
            req = signed.verify(self.verifier, now=now)
        except BoundaryError as exc:
            return self._reject(request_id, str(exc))
        except ValueError as exc:  # payload doesn't parse as an ActionRequest
            return self._reject(request_id, f"invalid request: {exc}")

        if req.policy_bundle != self.engine.bundle.sha256:
            return self._reject(req.id, f"policy drift: request decided under bundle {req.policy_bundle[:12]}, data plane has {self.engine.bundle.sha256[:12]}")

        controls = self.controls()
        decision = self.engine.evaluate(
            PolicyInput(
                agent=req.agent, capability=req.capability, env=req.env, pattern=req.pattern, rule=req.rule, approval=req.approval,
                enabled=bool(controls.get("enabled", True)), dry_run=bool(controls.get("dry_run", False)),
                reruns_today=self._reruns_today(req, now), actions_last_hour=self.ledger.count_since(now - timedelta(hours=1), RATE_LIMITED),
                max_reruns_per_day=req.params.get("max_reruns_per_day"),
            )
        )
        if not decision.allow:
            return self._reject(req.id, "policy: " + "; ".join(decision.reasons), level=decision.level, mode=decision.mode)

        try:
            checked = self.router.check_preconditions(req)
        except (PreconditionFailed, RouteError) as exc:
            return self._reject(req.id, f"precondition: {exc}")
        except Exception as exc:  # e.g. the platform doesn't know the run: refuse, never guess
            return self._reject(req.id, f"precondition check failed: {type(exc).__name__}: {redact(str(exc)).text[:300]}")

        if decision.mode == "dry_run":
            return ActionResult(request_id=req.id, status="dry_run", output={"preconditions": checked, "level": decision.level})

        # platform:pipeline:run[:other target values], so caps can count per pipeline by prefix
        extra = [str(req.target[k]) for k in sorted(req.target) if k not in ("pipeline_id", "run_id")]
        target = ":".join([req.platform_id, str(req.target.get("pipeline_id", "-")), str(req.target.get("run_id", "-")), *extra])
        reservation = self.ledger.reserve(req.idempotency_key, req.id, req.capability.value, target, now=now)
        if not reservation.created:
            status = {"SUCCESS": "success", "FAILED": "failed"}.get(reservation.state, "unknown")
            return ActionResult(
                request_id=req.id, status=status, output={**reservation.result, "duplicate_of": req.idempotency_key, "ledger_state": reservation.state},
            )

        try:
            output = self.router.execute(req)
        except Exception as exc:  # the platform refused or errored: nothing to confirm
            error = redact(f"{type(exc).__name__}: {exc}").text[:1000]
            self.ledger.finish(req.idempotency_key, "FAILED", {"error": error}, now=self.clock())
            return ActionResult(request_id=req.id, status="failed", error=error)

        try:
            self.router.verify(req, output)
        except Exception as exc:
            self.ledger.finish(req.idempotency_key, "UNKNOWN", {**output, "postcondition": str(exc)}, now=self.clock())
            return ActionResult(request_id=req.id, status="unknown", output=output, error=f"post-condition: {exc}")

        self.ledger.finish(req.idempotency_key, "SUCCESS", output, now=self.clock())
        ref = self.evidence.put_text(
            json.dumps({"request": req.payload(), "output": output}, indent=2, default=str),
            kind="action_output", source=ACTOR, incident_id=req.incident_id, media_type="application/json",
        )
        if req.capability in WATCHED and output.get("run_id"):
            self.events.emit(
                "watch", req.platform_id, f"{req.platform_id}:{req.target.get('pipeline_id', '-')}:{output['run_id']}",
                {
                    "request_id": req.id, "capability": req.capability.value, "run_id": str(output["run_id"]),
                    "incident_id": req.incident_id, "since": now.isoformat(),
                },
                now=self.clock(),
            )
        return ActionResult(request_id=req.id, status="success", output={**output, "preconditions": checked}, evidence_ids=[ref.id])

    def _reruns_today(self, req: ActionRequest, now: datetime) -> int:
        if req.capability.value not in RATE_LIMITED or "pipeline_id" not in req.target:
            return 0
        return self.ledger.count_since(
            now - timedelta(hours=24), (CapabilityId.RERUN.value, CapabilityId.REPAIR.value), f"{req.platform_id}:{req.target['pipeline_id']}:"
        )

