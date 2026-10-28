"""Triage agent: KEDB first, LLM only when the curated knowledge has no answer.

  1. Known Error DB match on the (redacted) failure evidence -> class, runbook, provenance.
  2. No match, or the class is still unknown -> one LLM diagnosis (budgeted, evidence framed as
     untrusted). The deterministic class still decides what may happen; the LLM explains.
"""

from __future__ import annotations

import logging
from typing import Any

from ...llm.gateway import BudgetExceeded
from ..deps import ControlPlaneDeps
from ..state import IncidentState

log = logging.getLogger(__name__)


def evidence_text(state: IncidentState, deps: ControlPlaneDeps) -> str:
    parts = []
    for evidence_id in state.get("evidence_ids") or []:
        try:
            parts.append(deps.evidence.get_text(evidence_id))
        except (OSError, ValueError) as exc:  # missing or tampered blob: never trust it
            log.warning("evidence %s unusable: %s", evidence_id[:12], exc)
    return "\n".join(parts) or (state.get("hints") or {}).get("error_headline") or ""


def triage(state: IncidentState, deps: ControlPlaneDeps) -> dict[str, Any]:
    if state["kind"] == "slow":
        return {"error_class": "slow", "matched_rule": None, "kedb": None, "diagnosis": None}
    if state["kind"] == "manual":
        return {"error_class": "manual", "matched_rule": None, "kedb": None, "diagnosis": None}
    if state["kind"] == "fix":  # triaged when the incident was opened
        hints = state.get("hints") or {}
        return {"error_class": hints.get("error_class") or "code", "matched_rule": hints.get("matched_rule"), "kedb": None, "diagnosis": None}

    hints = state.get("hints") or {}
    text = evidence_text(state, deps)
    platform = _platform_name(state, deps)
    entry = deps.kedb.match(text, platform)
    error_class = entry.error_class if entry else (hints.get("error_class") or "unknown")
    update: dict[str, Any] = {
        "error_class": error_class,
        "matched_rule": entry.name if entry else hints.get("matched_rule"),
        "kedb": entry.model_dump() if entry else None,
        "diagnosis": None,
    }
    if deps.triage is None or (entry is not None and error_class in ("transient", "infra")):
        return update  # curated knowledge is enough; save the LLM budget (code fixes need the root cause)

    try:
        diag = deps.triage.diagnose(
            incident_id=state.get("incident_id"), platform=state["platform_id"], pipeline=state["pipeline_name"],
            env=state["env"], error_text=text, rule_class=error_class, matched_rule=update["matched_rule"],
        )
    except BudgetExceeded as exc:
        update["diagnosis"] = {"error": f"budget: {exc}", "budget_stop": True}
        return update
    except Exception as exc:  # LLM problems never stop the incident
        update["diagnosis"] = {"error": f"llm unavailable: {str(exc)[:300]}"}
        return update
    update["diagnosis"] = diag.model_dump(mode="json")
    return update


def _platform_name(state: IncidentState, deps: ControlPlaneDeps) -> str:
    for pc in deps.config.platforms:
        if pc.id == state["platform_id"]:
            return pc.plugin
    return "*"
