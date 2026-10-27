"""State of one incident thread in the LangGraph orchestrator (checkpointed after every node)."""

from __future__ import annotations

from typing import Any, TypedDict


class Step(TypedDict, total=False):
    capability: str  # boundary.CapabilityId value
    agent: str  # who asks (must be registered for the capability)
    target: dict[str, Any]
    params: dict[str, Any]
    watch: bool  # wait for the run to finish (rerun / repair / smoke / tests)
    expect: dict[str, Any]  # reconciliation expectations for data.profile steps


class IncidentState(TypedDict, total=False):
    # intake
    work_item_id: int
    kind: str  # failure | slow
    platform_id: str
    pipeline_id: str
    pipeline_name: str
    run_id: str
    run_key: str
    env: str
    url: str | None
    parameters: dict[str, str]
    evidence_ids: list[str]
    hints: dict[str, Any]  # observer's classifier hints (error_class, matched_rule, failed tasks)
    incident_id: int
    # triage
    error_class: str  # transient | code | infra | unknown | slow
    matched_rule: str | None
    kedb: dict[str, Any] | None
    diagnosis: dict[str, Any] | None
    # plan + execution
    runbook: str  # rerun | cancel_rerun | repair | code_fix | escalate
    plan_reason: str
    steps: list[Step]
    step: int  # index of the current step
    decision: dict[str, Any]
    approval: dict[str, Any] | None  # boundary.Approval as dict, reused by later steps of the same plan
    approval_id: int | None
    request_id: str | None
    results: list[dict[str, Any]]  # one per finished step: {capability, status, output, error, run}
    # outcome
    outcome: str  # resolved | escalated | recommended | awaiting_code_fix
    outcome_reason: str
