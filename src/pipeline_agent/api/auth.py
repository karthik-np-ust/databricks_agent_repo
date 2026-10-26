"""Console authentication and role-based access.

Roles (each includes the ones below it):
  viewer   read everything except secrets (there are none in the API anyway)
  operator cancel/rerun runs, re-run triage, take over incidents
  approver start agent fixes (the PR approval itself always happens in GitHub)
  admin    runtime controls: kill switch, dry run

Demo mode (default): a login form issues an HMAC-signed session token for any
name + role. That proves the RBAC model end to end, but it is NOT authentication.
Pilot/enterprise: AGENT_CONSOLE_AUTH=oidc with Entra ID; roles come from
Entra groups (see docs/14-console.md). The checks below stay the same.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

ROLES = ("viewer", "operator", "approver", "admin")


class Principal(BaseModel):
    name: str
    role: str

    def has(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenSigner:
    def __init__(self, secret: str | None = None, ttl_seconds: int = 8 * 3600):
        # Without a configured secret, sessions last until the console restarts.
        self._key = (secret or secrets.token_hex(32)).encode()
        self.ttl = ttl_seconds

    def issue(self, name: str, role: str) -> str:
        payload = _b64(json.dumps({"sub": name, "role": role, "exp": int(time.time()) + self.ttl}).encode())
        sig = _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{sig}"

    def verify(self, token: str) -> Principal:
        try:
            payload, sig = token.split(".", 1)
        except ValueError:
            raise HTTPException(401, "malformed token") from None
        expected = _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(401, "invalid token")
        data = json.loads(_unb64(payload))
        if data["exp"] < time.time():
            raise HTTPException(401, "session expired")
        if data["role"] not in ROLES:
            raise HTTPException(401, "unknown role")
        return Principal(name=data["sub"], role=data["role"])


def current_user(request: Request) -> Principal:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "not signed in")
    return request.app.state.ctx.signer.verify(header.removeprefix("Bearer "))


def require(role: str):
    def _check(user: Principal = Depends(current_user)) -> Principal:
        if not user.has(role):
            raise HTTPException(403, f"requires role '{role}' (you are '{user.role}')")
        return user

    return _check
