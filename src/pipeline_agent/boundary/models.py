"""ActionRequest: the only thing that crosses the trust boundary from the control plane.

An agent never executes anything. It asks for a capability; the policy decides the autonomy
level; the control plane signs the request; the data plane verifies the signature, expiry and
policy bundle hash, re-checks policy and preconditions locally, and only then executes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..sdk.models import utcnow
from ..security.signing import SignatureError, Signer, Verifier
from .capabilities import CapabilityId


class BoundaryError(Exception):
    """A request the data plane must refuse (bad signature, expired, policy drift, ...)."""


class Approval(BaseModel):
    kind: Literal["console", "github_merge", "policy"]  # policy: pre-approved by autonomy level (L4/L5)
    ref: str  # approval id, PR url, or the policy rule
    by: str | None = None


class ActionRequest(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    idempotency_key: str  # deterministic: "<incident>:<step>:<attempt>" so graph replays can't double-execute
    capability: CapabilityId
    platform_id: str
    env: str
    target: dict[str, Any]  # e.g. {"pipeline_id": "...", "run_id": "..."}
    params: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    agent: str
    incident_id: int | None = None
    level: int  # autonomy level the policy granted
    policy_bundle: str  # sha256 of the policy bundle the decision was made with
    approval: Approval
    reason: str = ""
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime

    @classmethod
    def new(cls, ttl: timedelta = timedelta(minutes=30), **fields: Any) -> "ActionRequest":
        issued = fields.pop("issued_at", None) or utcnow()
        return cls(issued_at=issued, expires_at=issued + ttl, **fields)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SignedActionRequest(BaseModel):
    payload: dict[str, Any]
    signature: str
    key_id: str

    @classmethod
    def sign(cls, request: ActionRequest, signer: Signer) -> "SignedActionRequest":
        payload = request.payload()
        return cls(payload=payload, signature=signer.sign(payload), key_id=signer.key_id)

    def verify(self, verifier: Verifier, now: datetime | None = None) -> ActionRequest:
        try:
            verifier.verify(self.payload, self.signature, self.key_id)
        except SignatureError as exc:
            raise BoundaryError(str(exc)) from exc
        request = ActionRequest.model_validate(self.payload)
        if (now or utcnow()) >= request.expires_at:
            raise BoundaryError(f"request {request.id} expired at {request.expires_at:%Y-%m-%d %H:%M:%S}Z")
        return request


class ActionResult(BaseModel):
    request_id: str
    status: Literal["success", "failed", "unknown", "rejected", "dry_run"]
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
