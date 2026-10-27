"""Structured evidence and results flowing *up* the trust boundary (data plane -> control plane).

The data plane appends events (run_failed, run_slow, run_finished, ...); the control plane
consumes them. ``watch`` events are the data plane's own bookkeeping: runs it started and
must report on once they finish.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from ..sdk.models import utcnow
from ..store.db import EventRow, StateStore


class EventLog:
    def __init__(self, store: StateStore):
        self.store = store

    def emit(
        self, kind: str, platform_id: str, run_key: str | None, payload: dict[str, Any], evidence_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> int:
        with self.store.session() as s:
            row = EventRow(ts=now or utcnow(), kind=kind, platform_id=platform_id, run_key=run_key, payload=payload, evidence_ids=evidence_ids or [])
            s.add(row)
            s.commit()
            return row.id

    def exists(self, kind: str, run_key: str) -> bool:
        with self.store.session() as s:
            return s.scalar(select(EventRow.id).where(EventRow.kind == kind, EventRow.run_key == run_key).limit(1)) is not None

    def unconsumed(self, kinds: tuple[str, ...]) -> list[EventRow]:
        with self.store.session() as s:
            return list(
                s.scalars(select(EventRow).where(EventRow.kind.in_(kinds), EventRow.consumed_at.is_(None)).order_by(EventRow.id)).all()
            )

    def consume(self, event_ids: list[int], now: datetime | None = None) -> None:
        if not event_ids:
            return
        with self.store.session() as s:
            s.execute(update(EventRow).where(EventRow.id.in_(event_ids)).values(consumed_at=now or utcnow()))
            s.commit()

    def list(self, kind: str | None = None, limit: int = 100) -> list[EventRow]:
        with self.store.session() as s:
            q = select(EventRow).order_by(EventRow.id.desc()).limit(limit)
            if kind:
                q = q.where(EventRow.kind == kind)
            return list(s.scalars(q).all())
