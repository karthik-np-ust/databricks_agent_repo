"""Everything the control-plane graph needs, wired once (runtime.build_control_plane, or tests).

Deliberately absent: platform instances and credentials. The control plane only reads the
shared store (events, evidence, KEDB) and writes signed requests to the queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from ..autonomy import PolicyEngine
from ..boundary.events import EventLog
from ..boundary.queue import ActionQueue
from ..config import AgentConfig
from ..data_plane.ledger import ExecutionLedger
from ..evidence import EvidenceStore
from ..kedb import KnownErrorDB
from ..llm.triage import Triage
from ..notify.webhook import Notifier
from ..sdk.models import utcnow
from ..security.audit import AuditLogger
from ..security.signing import Signer
from ..store.db import StateStore
from .approvals import Approvals
from .registry import AgentRegistry


@dataclass
class ControlPlaneDeps:
    config: AgentConfig
    store: StateStore
    engine: PolicyEngine
    registry: AgentRegistry
    signer: Signer
    evidence: EvidenceStore
    notifier: Notifier
    triage: Triage | None = None  # None: no LLM, KEDB and classifier hints only
    controls: Callable[[], dict[str, Any]] = field(default=lambda: {"enabled": True, "dry_run": False})
    clock: Callable[[], datetime] = utcnow
    request_ttl: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        self.queue = ActionQueue(self.store)
        self.events = EventLog(self.store)
        self.ledger = ExecutionLedger(self.store)  # read-only here: counters for the policy
        self.kedb = KnownErrorDB(self.store)
        self.approvals = Approvals(self.store)
        self.audit = AuditLogger(self.store)
