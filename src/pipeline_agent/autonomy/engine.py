"""Policy decision point: autonomy level for one requested action.

Pure and deterministic (OPA-style: input -> decision). It never touches a platform or the DB;
callers pass the counters (reruns today, actions in the last hour) and runtime controls in.
The same bundle is evaluated by the control plane before signing and again by the data plane
before executing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, Field

from ..boundary.capabilities import CAPABILITIES, CapabilityId
from ..boundary.models import Approval
from ..security.signing import canonical_json

RATE_LIMITED = {CapabilityId.RERUN, CapabilityId.REPAIR, CapabilityId.CANCEL}


def _matches(selector: str | list[str] | None, value: str | None) -> bool:
    if selector is None or selector == "*":
        return True
    options = selector if isinstance(selector, list) else [selector]
    return value in options


class PolicyRule(BaseModel):
    name: str
    level: int = Field(ge=0, le=5)
    agent: str | list[str] | None = None
    capability: str | list[str] | None = None
    env: str | list[str] | None = None
    pattern: str | list[str] | None = None  # failure pattern: transient | code | infra | unknown | slow
    rule: str | list[str] | None = None  # KEDB / classifier rule name
    approval_from: list[Literal["console", "github_merge"]] = Field(default_factory=lambda: ["console"])

    def matches(self, inp: "PolicyInput") -> bool:
        return (
            _matches(self.agent, inp.agent)
            and _matches(self.capability, inp.capability.value)
            and _matches(self.env, inp.env)
            and _matches(self.pattern, inp.pattern)
            and _matches(self.rule, inp.rule)
        )


class Limits(BaseModel):
    max_reruns_per_day: int = 2
    max_actions_per_hour: int = 10


class PolicyBundle(BaseModel):
    version: int = 1
    defaults: dict[str, Any] = Field(default_factory=lambda: {"level": 1})
    rules: list[PolicyRule] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    sha256: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyBundle":
        bundle = cls.model_validate(data)
        bundle.sha256 = hashlib.sha256(canonical_json(data)).hexdigest()
        return bundle

    @classmethod
    def load(cls, path: Path) -> "PolicyBundle":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


class PolicyInput(BaseModel):
    agent: str
    capability: CapabilityId
    env: str
    pattern: str | None = None
    rule: str | None = None
    approval: Approval | None = None
    enabled: bool = True  # kill switch
    dry_run: bool = False
    reruns_today: int = 0
    actions_last_hour: int = 0
    max_reruns_per_day: int | None = None  # per-pipeline override of the bundle limit


Mode = Literal["execute", "dry_run", "await_approval", "recommend", "escalate", "manual", "deny"]


class PolicyDecision(BaseModel):
    allow: bool
    level: int
    mode: Mode
    needs_approval: bool = False
    approval_from: list[str] = Field(default_factory=list)
    rule: str
    reasons: list[str] = Field(default_factory=list)
    bundle: str  # sha256 of the bundle that decided


class AgentPermissions(Protocol):
    def allows(self, agent: str, capability: CapabilityId) -> bool: ...


class PolicyEngine:
    def __init__(self, bundle: PolicyBundle, registry: AgentPermissions | None = None):
        self.bundle = bundle
        self.registry = registry

    def _decision(self, allow: bool, level: int, mode: Mode, rule: str, reasons: list[str], **kw: Any) -> PolicyDecision:
        return PolicyDecision(allow=allow, level=level, mode=mode, rule=rule, reasons=reasons, bundle=self.bundle.sha256, **kw)

    def evaluate(self, inp: PolicyInput) -> PolicyDecision:
        if not inp.enabled:
            return self._decision(False, 0, "manual", "kill_switch", ["kill switch is on: everything is manual"])
        if self.registry is not None and not self.registry.allows(inp.agent, inp.capability):
            return self._decision(False, 0, "deny", "registry", [f"agent {inp.agent!r} is not registered for {inp.capability}"])

        rule = next((r for r in self.bundle.rules if r.matches(inp)), None)
        level = rule.level if rule else int(self.bundle.defaults.get("level", 1))
        name = rule.name if rule else "default"
        approval_from = list(rule.approval_from) if rule else ["console"]
        reasons = [f"rule {name} -> L{level}"]

        if inp.capability in RATE_LIMITED and level > 1:
            cap = inp.max_reruns_per_day if inp.max_reruns_per_day is not None else self.bundle.limits.max_reruns_per_day
            if inp.capability != CapabilityId.CANCEL and inp.reruns_today >= cap:
                level, reasons = 1, [*reasons, f"rerun cap reached ({inp.reruns_today}/{cap} in 24h) -> L1"]
            elif inp.actions_last_hour >= self.bundle.limits.max_actions_per_hour:
                limit = self.bundle.limits.max_actions_per_hour
                level, reasons = 1, [*reasons, f"circuit breaker open ({inp.actions_last_hour}/{limit} actions in 1h) -> L1"]

        read_only = not CAPABILITIES[inp.capability].side_effects
        if level == 0:
            return self._decision(False, 0, "manual", name, reasons)
        if level == 1 and not read_only:
            return self._decision(False, 1, "escalate", name, [*reasons, "evidence only; a human decides"])
        if level == 2 and not read_only:
            return self._decision(False, 2, "recommend", name, [*reasons, "recommend only; a human acts"])
        if level == 3 and not read_only:
            if inp.approval is None or inp.approval.kind not in approval_from:
                return self._decision(
                    False, 3, "await_approval", name, [*reasons, f"needs approval from {' or '.join(approval_from)}"],
                    needs_approval=True, approval_from=approval_from,
                )
            reasons.append(f"approved via {inp.approval.kind} ({inp.approval.ref})")
        mode: Mode = "dry_run" if inp.dry_run and not read_only else "execute"
        return self._decision(True, level, mode, name, reasons, approval_from=approval_from if level == 3 else [])
