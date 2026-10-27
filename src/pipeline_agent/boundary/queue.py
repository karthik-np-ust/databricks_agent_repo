"""The request queue across the trust boundary (a DB table the data plane pulls from).

Outbound-only for the data plane: it polls, claims one request at a time and reports results.
Submitting is idempotent on the request's idempotency key, so a replayed graph node gets the
original request back instead of queueing a second one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..sdk.models import utcnow
from ..store.db import ActionRequestRow, ActionResultRow, StateStore, _aware
from .models import ActionRequest, ActionResult, SignedActionRequest


class ActionQueue:
    def __init__(self, store: StateStore):
        self.store = store

    def submit(self, signed: SignedActionRequest) -> tuple[str, bool]:
        """Queue a signed request. Returns (request id, created); an existing key returns the original id."""
        req = ActionRequest.model_validate(signed.payload)
        existing = self.by_key(req.idempotency_key)
        if existing is not None:
            return existing.id, False
        row = ActionRequestRow(
            id=req.id, idempotency_key=req.idempotency_key, created_at=req.issued_at, expires_at=req.expires_at,
            incident_id=req.incident_id, agent=req.agent, capability=req.capability.value, platform_id=req.platform_id,
            env=req.env, level=req.level, status="queued", payload=signed.payload, signature=signed.signature,
            key_id=signed.key_id,
        )
        try:
            with self.store.session() as s:
                s.add(row)
                s.commit()
        except IntegrityError:  # lost a race with an identical submit
            return self.by_key(req.idempotency_key).id, False
        return req.id, True

    def by_key(self, idempotency_key: str) -> ActionRequestRow | None:
        with self.store.session() as s:
            return s.scalar(select(ActionRequestRow).where(ActionRequestRow.idempotency_key == idempotency_key))

    def get(self, request_id: str) -> ActionRequestRow | None:
        with self.store.session() as s:
            return s.get(ActionRequestRow, request_id)

    def claim_next(self, worker: str, now: datetime | None = None) -> SignedActionRequest | None:
        """Atomically move the oldest queued request to `claimed` (safe with several workers)."""
        now = now or utcnow()
        with self.store.session() as s:
            candidates = s.scalars(
                select(ActionRequestRow.id).where(ActionRequestRow.status == "queued").order_by(ActionRequestRow.created_at).limit(5)
            ).all()
            for request_id in candidates:
                claimed = s.execute(
                    update(ActionRequestRow)
                    .where(ActionRequestRow.id == request_id, ActionRequestRow.status == "queued")
                    .values(status="claimed", claimed_by=worker, claimed_at=now)
                ).rowcount
                s.commit()
                if claimed:
                    row = s.get(ActionRequestRow, request_id)
                    return SignedActionRequest(payload=row.payload, signature=row.signature, key_id=row.key_id)
        return None

    def complete(self, result: ActionResult, now: datetime | None = None) -> None:
        status = "rejected" if result.status == "rejected" else "done"
        with self.store.session() as s:
            s.add(
                ActionResultRow(
                    request_id=result.request_id, ts=now or utcnow(), status=result.status, output=result.output,
                    error=result.error, evidence_ids=result.evidence_ids,
                )
            )
            s.execute(update(ActionRequestRow).where(ActionRequestRow.id == result.request_id).values(status=status))
            s.commit()

    def result(self, request_id: str) -> ActionResult | None:
        with self.store.session() as s:
            row = s.scalar(
                select(ActionResultRow).where(ActionResultRow.request_id == request_id).order_by(ActionResultRow.id.desc()).limit(1)
            )
        if row is None:
            return None
        return ActionResult(request_id=row.request_id, status=row.status, output=row.output, error=row.error, evidence_ids=row.evidence_ids)

    def pending(self) -> list[ActionRequestRow]:
        with self.store.session() as s:
            rows = s.scalars(select(ActionRequestRow).where(ActionRequestRow.status.in_(("queued", "claimed")))).all()
        for r in rows:
            r.created_at, r.expires_at = _aware(r.created_at), _aware(r.expires_at)
        return list(rows)
