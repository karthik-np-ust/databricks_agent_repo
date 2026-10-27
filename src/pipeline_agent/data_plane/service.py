"""Data-plane process: observe, execute, sweep. One active instance at a time (lease).

    pipeline-agent dataplane          # loop every poll interval
    pipeline-agent dataplane --once   # one tick (demos, tests)
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from ..boundary.models import ActionResult
from ..leases import Leases
from ..sdk.models import utcnow
from .executor import Executor
from .observer import ObserveReport, Observer

LEASE = "data-plane"


@dataclass
class DataPlaneReport:
    started_at: datetime
    leader: bool = True
    observed: ObserveReport = field(default_factory=ObserveReport)
    results: list[ActionResult] = field(default_factory=list)
    swept_unknown: list[str] = field(default_factory=list)


class DataPlane:
    def __init__(
        self,
        observer: Observer,
        executor: Executor,
        leases: Leases,
        *,
        holder: str | None = None,
        lease_ttl: timedelta = timedelta(minutes=3),
        stale_after: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] = utcnow,
    ):
        self.observer = observer
        self.executor = executor
        self.leases = leases
        self.holder = holder or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_ttl = lease_ttl
        self.stale_after = stale_after
        self.clock = clock

    def tick(self) -> DataPlaneReport:
        now = self.clock()
        report = DataPlaneReport(started_at=now)
        if not self.leases.acquire(LEASE, self.holder, self.lease_ttl, now=now):
            report.leader = False  # another data plane is active
            return report
        report.observed = self.observer.tick()
        report.results = self.executor.drain()
        report.swept_unknown = self.executor.ledger.sweep_stale(self.stale_after, now=self.clock())
        return report

    def stop(self) -> None:
        self.leases.release(LEASE, self.holder)
