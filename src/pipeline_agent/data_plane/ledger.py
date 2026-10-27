"""Execution ledger: at most one execution per idempotency key.

    reserve -> execute -> finish(SUCCESS | FAILED)

``reserve`` is the only gate: it inserts the key or reports the state already recorded, so a
replayed graph node (or a duplicate request) never runs the side effect twice. If the process
dies between reserve and finish, the entry is swept to UNKNOWN: the platform call may or may
not have happened, so it is never retried automatically; a human or the reconciliation agent
looks at the platform and decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..sdk.models import utcnow
from ..store.db import LedgerRow, StateStore, _aware

State = Literal["RESERVED", "SUCCESS", "FAILED", "UNKNOWN"]


@dataclass(frozen=True)
class Reservation:
    key: str
    state: State
    created: bool  # True: this caller owns the execution; False: someone already did (or is doing) it
    result: dict[str, Any]


class ExecutionLedger:
    def __init__(self, store: StateStore):
        self.store = store

    def reserve(self, key: str, request_id: str, capability: str, target: str, now: datetime | None = None) -> Reservation:
        row = LedgerRow(
            idempotency_key=key, request_id=request_id, capability=capability, target=target[:300], state="RESERVED",
            reserved_at=now or utcnow(), result={},
        )
        try:
            with self.store.session() as s:
                s.add(row)
                s.commit()
            return Reservation(key, "RESERVED", True, {})
        except IntegrityError:
            existing = self.get(key)
            return Reservation(key, existing.state, False, existing.result or {})

    def count_since(self, since: datetime, capabilities: tuple[str, ...], target_prefix: str = "") -> int:
        """Executions (anything but FAILED) since ``since``: input for rerun caps and the circuit breaker."""
        with self.store.session() as s:
            q = select(func.count()).select_from(LedgerRow).where(
                LedgerRow.reserved_at >= since, LedgerRow.capability.in_(capabilities), LedgerRow.state != "FAILED"
            )
            if target_prefix:
                q = q.where(LedgerRow.target.startswith(target_prefix))
            return s.scalar(q) or 0

    def finish(
        self, key: str, state: Literal["SUCCESS", "FAILED", "UNKNOWN"], result: dict[str, Any] | None = None, now: datetime | None = None
    ) -> None:
        """UNKNOWN: the call went out but its effect couldn't be confirmed (post-condition failed)."""
        with self.store.session() as s:
            changed = s.execute(
                update(LedgerRow)
                .where(LedgerRow.idempotency_key == key, LedgerRow.state == "RESERVED")
                .values(state=state, finished_at=now or utcnow(), result=result or {})
            ).rowcount
            s.commit()
        if not changed:
            raise ValueError(f"ledger entry {key} is not RESERVED")

    def get(self, key: str) -> LedgerRow | None:
        with self.store.session() as s:
            row = s.get(LedgerRow, key)
        if row is not None:
            row.reserved_at, row.finished_at = _aware(row.reserved_at), _aware(row.finished_at)
        return row

    def sweep_stale(self, older_than: timedelta, now: datetime | None = None) -> list[str]:
        """RESERVED for too long = the executor died mid-call: mark UNKNOWN (never auto-retried)."""
        cutoff = (now or utcnow()) - older_than
        with self.store.session() as s:
            keys = list(
                s.scalars(select(LedgerRow.idempotency_key).where(LedgerRow.state == "RESERVED", LedgerRow.reserved_at < cutoff)).all()
            )
            if keys:
                s.execute(
                    update(LedgerRow)
                    .where(LedgerRow.idempotency_key.in_(keys), LedgerRow.state == "RESERVED")
                    .values(state="UNKNOWN", finished_at=now or utcnow(), result={"note": "executor stopped mid-call"})
                )
                s.commit()
        return keys
