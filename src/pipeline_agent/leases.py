"""Leases with expiry (stand-in for Valkey/Redis locks): one graph runner, one action per target.

A holder keeps a lease by renewing it before it expires; a crashed holder's lease simply runs
out and the next process takes over. Fixes the "two agents ran at once" gap (WORKLOG 2026-10-26).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, or_, update
from sqlalchemy.exc import IntegrityError

from .sdk.models import utcnow
from .store.db import LeaseRow, StateStore


class Leases:
    def __init__(self, store: StateStore):
        self.store = store

    def acquire(self, name: str, holder: str, ttl: timedelta, now: datetime | None = None) -> bool:
        """Take or renew the lease. False while another holder's lease is still valid."""
        now = now or utcnow()
        with self.store.session() as s:
            taken = s.execute(
                update(LeaseRow)
                .where(LeaseRow.name == name, or_(LeaseRow.holder == holder, LeaseRow.expires_at <= now))
                .values(holder=holder, acquired_at=now, expires_at=now + ttl)
            ).rowcount
            s.commit()
            if taken:
                return True
            try:
                s.add(LeaseRow(name=name, holder=holder, acquired_at=now, expires_at=now + ttl))
                s.commit()
                return True
            except IntegrityError:  # exists and held by someone else
                return False

    def release(self, name: str, holder: str) -> None:
        with self.store.session() as s:
            s.execute(delete(LeaseRow).where(LeaseRow.name == name, LeaseRow.holder == holder))
            s.commit()

    def holder(self, name: str, now: datetime | None = None) -> str | None:
        now = now or utcnow()
        with self.store.session() as s:
            row = s.get(LeaseRow, name)
        if row is None:
            return None
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=now.tzinfo)
        return row.holder if expires > now else None
