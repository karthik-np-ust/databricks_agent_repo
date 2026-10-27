"""Reconciliation agent (deterministic): did the recovery actually work?

Run state comes from the ``run_finished`` event of each watched step; data checks come from
``data.profile`` results compared with the pipeline's ``reconcile`` expectations.
"""

from __future__ import annotations

from typing import Any

from ..state import IncidentState


def profile_violations(output: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    table = output.get("table", "?")
    found = []
    if output.get("rows", 0) < expect.get("min_rows", 1):
        found.append(f"{table}: {output.get('rows', 0)} rows, expected at least {expect.get('min_rows', 1)}")
    if output.get("duplicate_keys", 0):
        found.append(f"{table}: {output['duplicate_keys']} duplicate key row(s)")
    for column, nulls in (output.get("nulls") or {}).items():
        if nulls:
            found.append(f"{table}: {nulls} null {column}")
    return found


def verdict(state: IncidentState) -> tuple[str, str]:
    """(outcome, reason) once every step has a result."""
    dry = [r["capability"] for r in state.get("results") or [] if r.get("status") == "dry_run"]
    if dry:
        return "recommended", f"dry run: would have run {', '.join(dry)}; nothing was executed"
    problems = []
    for result in state.get("results") or []:
        if result.get("violations"):
            problems += result["violations"]
        run = result.get("run")
        if run and run.get("status") != "succeeded":
            problems.append(f"{result['capability']} run {run.get('run_id')} ended {run.get('status')}")
    if problems:
        return "escalated", "reconciliation failed: " + "; ".join(problems)
    checks = sum(1 for r in state.get("results") or [] if r["capability"] == "data.profile")
    runs = [r["run"]["run_id"] for r in state.get("results") or [] if r.get("run")]
    detail = f"run {', '.join(runs)} succeeded" if runs else "actions completed"
    return "resolved", detail + (f"; {checks} data check(s) passed" if checks else "")
