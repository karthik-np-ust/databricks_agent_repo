"""Human approvals (L3). The graph creates a pending approval and interrupts; a human decides
in the console (or ``pipeline-agent approvals``); the runner resumes the graph with the decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..sdk.models import utcnow
from ..store.db import ApprovalRow, StateStore, _aware


class Approvals:
    def __init__(self, store: StateStore):
        self.store = store

    def request(
        self, thread_id: str, incident_id: int | None, capability: str, summary: str, proposal: dict[str, Any], now: datetime | None = None,
    ) -> ApprovalRow:
        """Idempotent per (thread, capability): a replayed node gets the same approval back."""
        with self.store.session() as s:
            existing = s.scalar(
                select(ApprovalRow).where(ApprovalRow.thread_id == thread_id, ApprovalRow.capability == capability).order_by(ApprovalRow.id.desc())
            )
            if existing is not None and existing.status == "pending":
                return existing
            row = ApprovalRow(
                created_at=now or utcnow(), thread_id=thread_id, incident_id=incident_id, capability=capability,
                summary=summary, proposal=proposal, status="pending",
            )
            s.add(row)
            s.commit()
            return row

    def decide(self, approval_id: int, approve: bool, by: str, comment: str | None = None, now: datetime | None = None) -> ApprovalRow:
        with self.store.session() as s:
            row = s.get(ApprovalRow, approval_id)
            if row is None:
                raise KeyError(f"approval {approval_id} not found")
            if row.status != "pending":
                raise ValueError(f"approval {approval_id} is already {row.status}")
            row.status = "approved" if approve else "rejected"
            row.decided_by, row.decided_at, row.comment = by, now or utcnow(), comment
            s.commit()
            return row

    def get(self, approval_id: int) -> ApprovalRow | None:
        with self.store.session() as s:
            row = s.get(ApprovalRow, approval_id)
        if row is not None:
            row.created_at, row.decided_at = _aware(row.created_at), _aware(row.decided_at)
        return row

    def list(self, status: str | None = "pending") -> list[ApprovalRow]:
        with self.store.session() as s:
            q = select(ApprovalRow).order_by(ApprovalRow.id.desc())
            if status:
                q = q.where(ApprovalRow.status == status)
            return list(s.scalars(q).all())
