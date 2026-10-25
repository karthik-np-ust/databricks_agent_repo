"""Policy engine: turns an observed run into a decision. It never calls a platform itself.

Rules (see docs/02-architecture.md for the full decision table):
  active run over threshold -> cancel + rerun, unless the rerun cap / circuit breaker /
                               missing capability / non-actionable env says escalate
  failed, transient         -> rerun up to transient_retries, then escalate
  failed, code              -> open incident (fix flow eligible when auto_fix + CODE capability)
  failed, infra or unknown  -> escalate (never patch code, never loop)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .config import AgentConfig, PipelinePolicy
from .sdk.capabilities import Capability
from .sdk.models import ErrorClass, FailureContext, Run, utcnow
from .sdk.plugin import PlatformInstance
from .store.db import StateStore


class ActionType(StrEnum):
    NONE = "none"
    NOTIFY = "notify"
    CANCEL_AND_RERUN = "cancel_and_rerun"
    RERUN = "rerun"
    OPEN_INCIDENT = "open_incident"
    ESCALATE = "escalate"


@dataclass
class Decision:
    action: ActionType
    reason: str
    trigger: str  # threshold | transient | failure
    policy: PipelinePolicy
    threshold_seconds: float | None = None
    error_class: ErrorClass | None = None
    fix_eligible: bool = False


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[idx]


class PolicyEngine:
    def __init__(self, config: AgentConfig, store: StateStore):
        self.config = config
        self.store = store

    def policy_for(self, run: Run) -> PipelinePolicy:
        return self.config.policy_for(run.platform_id, run.pipeline_name)

    def threshold_for(self, run: Run, policy: PipelinePolicy) -> float | None:
        if policy.max_duration_seconds:
            return float(policy.max_duration_seconds)
        samples = self.store.successful_durations(run.platform_id, run.pipeline_id)
        if len(samples) < policy.baseline_min_samples:
            return None
        return p95(samples) * policy.baseline_multiplier

    def _circuit_open(self, now: datetime) -> bool:
        recent = self.store.count_actions_since(now - timedelta(hours=1))
        return recent >= self.config.circuit_breaker.max_actions_per_hour

    def _reruns_today(self, run: Run, now: datetime, trigger: str | None = None) -> int:
        return self.store.count_reruns(run.platform_id, run.pipeline_id, now - timedelta(hours=24), trigger)

    def evaluate_active(self, run: Run, instance: PlatformInstance, now: datetime | None = None) -> Decision:
        now = now or utcnow()
        policy = self.policy_for(run)
        threshold = self.threshold_for(run, policy)
        elapsed = run.elapsed(now)
        if threshold is None or elapsed is None or elapsed.total_seconds() <= threshold:
            return Decision(ActionType.NONE, "within threshold", "threshold", policy, threshold)

        if self.store.actions_for_run(run.key):
            return Decision(ActionType.NONE, "already handled", "threshold", policy, threshold)

        over = f"running {int(elapsed.total_seconds())}s, threshold {int(threshold)}s"
        if run.env not in policy.act_in_envs:
            return Decision(ActionType.NOTIFY, f"{over}; env {run.env} is notify-only", "threshold", policy, threshold)
        if not (instance.supports(Capability.CANCEL) and instance.supports(Capability.RERUN)):
            return Decision(ActionType.ESCALATE, f"{over}; platform cannot cancel/rerun", "threshold", policy, threshold)
        if self._reruns_today(run, now) >= policy.max_reruns_per_day:
            return Decision(
                ActionType.ESCALATE,
                f"{over}; rerun cap of {policy.max_reruns_per_day}/day reached",
                "threshold",
                policy,
                threshold,
            )
        if self._circuit_open(now):
            return Decision(ActionType.ESCALATE, f"{over}; circuit breaker open", "threshold", policy, threshold)
        return Decision(ActionType.CANCEL_AND_RERUN, over, "threshold", policy, threshold)

    def evaluate_failure(
        self, run: Run, ctx: FailureContext, instance: PlatformInstance, now: datetime | None = None
    ) -> Decision:
        now = now or utcnow()
        policy = self.policy_for(run)
        if self.store.incident_for_run(run.key) or self.store.actions_for_run(run.key):
            return Decision(ActionType.NONE, "already handled", "failure", policy)

        cls = ctx.error_class
        rule = f" (rule: {ctx.matched_rule})" if ctx.matched_rule else ""
        if run.env not in policy.act_in_envs:
            return Decision(ActionType.NOTIFY, f"{cls} failure in notify-only env {run.env}{rule}", "failure", policy, error_class=cls)

        if cls == ErrorClass.TRANSIENT:
            if not instance.supports(Capability.RERUN):
                return Decision(ActionType.ESCALATE, f"transient failure{rule}; platform cannot rerun", "transient", policy, error_class=cls)
            if self._reruns_today(run, now, trigger="transient") >= policy.transient_retries:
                return Decision(
                    ActionType.ESCALATE,
                    f"transient failure{rule}; still failing after {policy.transient_retries} rerun(s)",
                    "transient",
                    policy,
                    error_class=cls,
                )
            if self._circuit_open(now):
                return Decision(ActionType.ESCALATE, f"transient failure{rule}; circuit breaker open", "transient", policy, error_class=cls)
            return Decision(ActionType.RERUN, f"transient failure{rule}", "transient", policy, error_class=cls)

        if cls == ErrorClass.CODE:
            eligible = policy.auto_fix and instance.supports(Capability.CODE)
            note = "fix flow eligible" if eligible else "auto-fix off or no code access"
            return Decision(ActionType.OPEN_INCIDENT, f"code/data failure{rule}; {note}", "failure", policy, error_class=cls, fix_eligible=eligible)

        what = "infra/permission/config" if cls == ErrorClass.INFRA else "unclassified"
        return Decision(ActionType.ESCALATE, f"{what} failure{rule}; needs a human", "failure", policy, error_class=cls)
