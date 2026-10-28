"""Planner: picks a runbook and turns it into concrete, policy-checkable steps. Proposes only.

Runbook source, in order: the KEDB entry's curated runbook, else the failure class.
Recovery steps come first; Reconciliation's read-only data checks run after them.
"""

from __future__ import annotations

from typing import Any

from ...boundary.capabilities import CapabilityId
from ...kedb import RUNBOOK_BY_CLASS
from ..deps import ControlPlaneDeps
from ..state import IncidentState, Step


def plan(state: IncidentState, deps: ControlPlaneDeps) -> dict[str, Any]:
    diag = state.get("diagnosis") or {}
    if diag.get("budget_stop"):
        return _plan("escalate", "LLM budget reached: a human takes over", [])

    if state["kind"] == "manual":
        m = state["manual"]
        step = Step(capability=m["capability"], agent="console", target=m["target"], params=m.get("params") or {},
                    watch=m["capability"] in (CapabilityId.RERUN.value, CapabilityId.REPAIR.value))
        update = _plan("manual", f"{m['capability']} requested by {m['requested_by']}: {m['reason']}", [step])
        update["approval"] = {"kind": "console", "ref": f"user:{m['requested_by']}", "by": m["requested_by"]}
        return update
    if state["kind"] == "fix":
        return _plan("code_fix", f"code fix requested by {(state.get('hints') or {}).get('requested_by')}", [])
    if state["kind"] == "slow":
        runbook = "cancel_rerun"
    else:
        runbook = (state.get("kedb") or {}).get("runbook") or RUNBOOK_BY_CLASS.get(state["error_class"], "escalate")

    pipeline = {"pipeline_id": state["pipeline_id"]}
    run = {**pipeline, "run_id": state["run_id"]}
    job_parameters = {"job_parameters": state.get("parameters") or {}}
    hints = state.get("hints") or {}
    if runbook == "rerun":
        steps = [Step(capability=CapabilityId.RERUN.value, agent="recovery", target=run, params=job_parameters, watch=True)]
        reason = f"{state['error_class']} failure ({state.get('matched_rule') or 'no rule'}): rerun"
    elif runbook == "repair":
        steps = [Step(capability=CapabilityId.REPAIR.value, agent="recovery", target=run, params={}, watch=True)]
        reason = f"{state['error_class']} failure ({state.get('matched_rule') or 'no rule'}): repair the failed tasks"
    elif runbook == "cancel_rerun":
        steps = [
            Step(capability=CapabilityId.CANCEL.value, agent="recovery", target=run, params={}, watch=False),
            # Pipeline-only target: the cancelled run may still be terminating.
            Step(capability=CapabilityId.RERUN.value, agent="recovery", target=pipeline, params=job_parameters, watch=True),
        ]
        reason = f"running {hints.get('elapsed_seconds')}s, threshold {hints.get('threshold_seconds')}s: cancel and rerun"
    elif runbook == "code_fix":
        return _plan("code_fix", f"code failure ({state.get('matched_rule') or 'no rule'}): needs a code fix", [])
    else:
        return _plan("escalate", f"{state['error_class']} failure ({state.get('matched_rule') or 'no rule'}): needs a human", [])
    return _plan(runbook, reason, steps + reconcile_steps(state, deps))


def reconcile_steps(state: IncidentState, deps: ControlPlaneDeps) -> list[Step]:
    policy = deps.config.policy_for(state["platform_id"], state["pipeline_name"])
    return [
        Step(
            capability=CapabilityId.DATA_PROFILE.value, agent="reconciliation",
            target={"pipeline_id": state["pipeline_id"], "table": check.table.format(env=state["env"])},
            params={"keys": check.keys, "not_null": check.not_null}, watch=False, expect={"min_rows": check.min_rows},
        )
        for check in policy.reconcile
    ]


def _plan(runbook: str, reason: str, steps: list[Step]) -> dict[str, Any]:
    return {"runbook": runbook, "plan_reason": reason, "steps": steps, "step": 0}
