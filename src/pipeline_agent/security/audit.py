"""Append-only audit trail. Every agent (and later, every human) action goes here."""

from __future__ import annotations

import logging
from typing import Any

from ..store.db import StateStore
from .redact import redact_text

log = logging.getLogger(__name__)

AGENT_ACTOR = "agent"


class AuditLogger:
    def __init__(self, store: StateStore):
        self.store = store

    def log(self, action: str, target: str, actor: str = AGENT_ACTOR, **details: Any) -> None:
        clean = {k: redact_text(v) if isinstance(v, str) else v for k, v in details.items()}
        self.store.append_audit(actor=actor, action=action, target=target, details=clean)
        log.info("audit %s %s %s %s", actor, action, target, clean)
