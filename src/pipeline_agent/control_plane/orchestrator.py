"""Agent orchestrator: one LangGraph thread per work item (incident).

    intake -> triage -> plan -> decide --(L4/L5)--> submit -> wait_result -> [wait_run] -> advance -+
                                  |                                                                 |
                                  +--(L3)--> request_approval -> wait_approval -> decide            |
                                  +--(L0-L2)--> close                     advance -> decide | reconcile -> close

Replay safety: a resumed node runs again from its start, so the nodes that wait
(``wait_*``) only call ``interrupt()`` and record what came back. Every side effect (incident
row, approval row, signed request) lives in its own node *before* a wait, and is idempotent:
incident per run key, approval per (thread, capability), request per deterministic key.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..autonomy import PolicyInput
from ..boundary.capabilities import CapabilityId
from ..boundary.models import ActionRequest, Approval, SignedActionRequest
from ..llm.context import condense_logs
from ..notify.webhook import Notification, Severity
from ..sdk.models import Env, Run, RunStatus
from ..store.db import WorkItemRow
from .agents.planner import plan as plan_steps
from .agents.reconciliation import profile_violations, verdict
from .agents.triage import evidence_text, triage as triage_agent
from .codefix import CodeFixNodes
from .deps import ControlPlaneDeps
from .state import IncidentState

RATE_LIMITED = (CapabilityId.RERUN.value, CapabilityId.REPAIR.value, CapabilityId.CANCEL.value)
RECORDED_ACTIONS = {CapabilityId.RERUN.value: "rerun", CapabilityId.REPAIR.value: "repair", CapabilityId.CANCEL.value: "cancel"}


def thread_id(work_item_id: int) -> str:
    return f"wi-{work_item_id}"


class IncidentGraph(CodeFixNodes):
    def __init__(self, deps: ControlPlaneDeps):
        self.d = deps

    # helpers -------------------------------------------------------------------
    def _run(self, s: IncidentState) -> Run:
        status = RunStatus.RUNNING if s["kind"] == "slow" else RunStatus.FAILED
        return Run(
            platform_id=s["platform_id"], pipeline_id=s["pipeline_id"], pipeline_name=s["pipeline_name"], run_id=s["run_id"],
            status=status, env=Env(s["env"]), url=s.get("url"), parameters=s.get("parameters") or {},
        )

    def _notify(self, s: IncidentState, title: str, summary: str, severity: Severity = Severity.INFO, **facts: Any) -> None:
        base = {"platform": s["platform_id"], "pipeline": s["pipeline_name"], "env": s["env"], "run_id": s["run_id"]}
        if s.get("incident_id"):
            base["incident"] = str(s["incident_id"])
        self.d.notifier.send(Notification(title, summary, severity, {**base, **{k: str(v) for k, v in facts.items()}}, s.get("url")))

    def _audit(self, s: IncidentState, action: str, **details: Any) -> None:
        self.d.audit.log(action, s["run_key"], actor="agent", incident_id=s.get("incident_id"), work_item=s["work_item_id"], **details)

    def _policy(self, s: IncidentState) -> Any:
        return self.d.config.policy_for(s["platform_id"], s["pipeline_name"])

    def _rerun_cap(self, s: IncidentState) -> int:
        policy = self._policy(s)
        return policy.transient_retries if s.get("error_class") == "transient" else policy.max_reruns_per_day

    # nodes ---------------------------------------------------------------------
    def intake(self, s: IncidentState) -> dict[str, Any]:
        existing = self.d.store.incident_for_run(s["run_key"])
        if existing is not None:  # replay after a crash: the incident row is already there
            return {"incident_id": existing.id}
        hints = s.get("hints") or {}
        slow = s["kind"] == "slow"
        message = (
            f"running {hints.get('elapsed_seconds')}s, threshold {hints.get('threshold_seconds')}s"
            if slow else condense_logs(evidence_text(s, self.d), 4000)
        )
        cls = "slow" if slow else (hints.get("error_class") or "unknown")
        policy = self._policy(s)
        incident = self.d.store.create_incident(
            self._run(s), status="open", error_class=cls, error_message=message, matched_rule=hints.get("matched_rule"),
            fix_eligible=bool(not slow and cls == "code" and policy.auto_fix), now=self.d.clock(),
        )
        s = {**s, "incident_id": incident.id}
        self._audit(s, "incident_opened", error_class=cls, kind=s["kind"])
        title = f"Pipeline over threshold: {s['pipeline_name']}" if slow else f"Pipeline failed: {s['pipeline_name']}"
        first = next((ln for ln in message.splitlines() if ln.strip()), "no error text")
        self._notify(s, title, first[:300], Severity.CRITICAL if s["env"] == "prod" else Severity.WARNING, error_class=cls)
        return {"incident_id": incident.id}

    def triage(self, s: IncidentState) -> dict[str, Any]:
        update = triage_agent(s, self.d)
        fields: dict[str, Any] = {"error_class": update["error_class"], "matched_rule": update["matched_rule"]}
        diag = update.get("diagnosis")
        if diag and "summary" in diag:
            _, tokens, cost = self.d.store.llm_usage(incident_id=s["incident_id"])
            fields.update(diagnosis=diag, suspicious_content=int(diag.get("suspicious_content", False)), llm_tokens=tokens, llm_cost_usd=cost)
            self._audit(s, "diagnosed", model=diag.get("model"), llm_class=diag.get("error_class"), confidence=diag.get("confidence"))
            self._notify(s, f"Diagnosis: {s['pipeline_name']}", diag.get("summary", ""), Severity.CRITICAL if diag.get("suspicious_content") else Severity.INFO)
        elif diag:
            self._audit(s, "llm_budget_stop" if diag.get("budget_stop") else "llm_unavailable", error=diag["error"])
        if update.get("kedb"):
            kedb = update["kedb"]
            self._audit(s, "kedb_matched", entry=kedb["name"], runbook=kedb["runbook"], provenance=kedb["provenance"])
        self.d.store.update_incident(s["incident_id"], now=self.d.clock(), **fields)
        return update

    def plan(self, s: IncidentState) -> dict[str, Any]:
        update = plan_steps(s, self.d)
        self._audit(s, "planned", runbook=update["runbook"], reason=update["plan_reason"], steps=[st["capability"] for st in update["steps"]])
        if not update["steps"]:
            if update["runbook"] == "code_fix" and self._can_fix(s):
                return update  # Recovery writes the fix, the Testing agent proves it
            outcome = "awaiting_code_fix" if update["runbook"] == "code_fix" else "escalated"
            update.update(outcome=outcome, outcome_reason=update["plan_reason"])
        return update

    def _can_fix(self, s: IncidentState) -> bool:
        incident = self.d.store.get_incident(s["incident_id"])
        return self.d.code_fix_enabled and bool(incident and incident.fix_eligible)

    def decide(self, s: IncidentState) -> dict[str, Any]:
        step = s["steps"][s["step"]]
        now = self.d.clock()
        controls = self.d.controls()
        prefix = f"{s['platform_id']}:{s['pipeline_id']}:"
        decision = self.d.engine.evaluate(
            PolicyInput(
                agent=step["agent"], capability=CapabilityId(step["capability"]), env=step.get("env", s["env"]), pattern=s["error_class"],
                rule=s.get("matched_rule"), approval=Approval(**s["approval"]) if s.get("approval") else None,
                enabled=bool(controls.get("enabled", True)), dry_run=bool(controls.get("dry_run", False)),
                reruns_today=self.d.ledger.count_since(now - timedelta(hours=24), (CapabilityId.RERUN.value, CapabilityId.REPAIR.value), prefix),
                actions_last_hour=self.d.ledger.count_since(now - timedelta(hours=1), RATE_LIMITED),
                max_reruns_per_day=self._rerun_cap(s),
            )
        )
        update: dict[str, Any] = {"decision": decision.model_dump()}
        if decision.mode == "await_approval" and s.get("approval"):
            # Approved already, but not by a kind this rule accepts (e.g. prod code needs the GitHub merge).
            update.update(outcome="escalated", outcome_reason=f"{step['capability']}: approval via {s['approval']['kind']} is not enough; "
                          + "; ".join(decision.reasons))
            decision = decision.model_copy(update={"mode": "escalate"})
            update["decision"] = decision.model_dump()
        elif not decision.allow and decision.mode != "await_approval":
            outcome = "recommended" if decision.mode == "recommend" else "escalated"
            update.update(outcome=outcome, outcome_reason=f"{step['capability']}: " + "; ".join(decision.reasons))
        self._audit(s, "policy_decision", capability=step["capability"], level=decision.level, mode=decision.mode, reasons=decision.reasons)
        return update

    def request_approval(self, s: IncidentState) -> dict[str, Any]:
        step = s["steps"][s["step"]]
        remaining = [st["capability"] for st in s["steps"][s["step"]:]]
        summary = f"{s['plan_reason']} ({', '.join(remaining)}) on {s['pipeline_name']} [{s['env']}]"
        row = self.d.approvals.request(
            thread_id(s["work_item_id"]), s.get("incident_id"), step["capability"], summary,
            {"steps": s["steps"][s["step"]:], "reasons": s["decision"]["reasons"], "level": s["decision"]["level"]}, now=self.d.clock(),
        )
        self._audit(s, "approval_requested", approval_id=row.id, capability=step["capability"])
        self._notify(s, f"Approval needed: {s['pipeline_name']}", summary, Severity.WARNING, approval=row.id)
        return {"approval_id": row.id}

    def wait_approval(self, s: IncidentState) -> dict[str, Any]:
        answer = interrupt({"wait": "approval", "approval_id": s["approval_id"]})
        if answer.get("status") != "approved":
            why = "rejected by " + str(answer.get("by")) if answer.get("status") == "rejected" else "approval timed out"
            return {"outcome": "escalated", "outcome_reason": f"{why}: {answer.get('comment') or ''}".strip(": ")}
        return {"approval": {"kind": "console", "ref": f"approval:{s['approval_id']}", "by": answer.get("by")}}

    def submit(self, s: IncidentState) -> dict[str, Any]:
        step, decision = s["steps"][s["step"]], s["decision"]
        approval = s.get("approval") if decision["level"] == 3 else None
        request = ActionRequest.new(
            ttl=self.d.request_ttl, issued_at=self.d.clock(),
            idempotency_key=f"{thread_id(s['work_item_id'])}:{len(s.get('results', []))}:{step['capability']}",
            capability=step["capability"], platform_id=s["platform_id"], env=step.get("env", s["env"]), target=step["target"],
            params={**step.get("params", {}), "max_reruns_per_day": self._rerun_cap(s)}, agent=step["agent"],
            incident_id=s.get("incident_id"), level=decision["level"], policy_bundle=decision["bundle"], pattern=s["error_class"],
            rule=s.get("matched_rule"), approval=Approval(**approval) if approval else Approval(kind="policy", ref=decision["rule"]),
            reason=s.get("plan_reason", ""),
        )
        request_id, created = self.d.queue.submit(SignedActionRequest.sign(request, self.d.signer))
        self._audit(s, "action_requested", request_id=request_id, capability=step["capability"], level=decision["level"], replay=not created)
        return {"request_id": request_id}

    def wait_result(self, s: IncidentState) -> dict[str, Any]:
        step = s["steps"][s["step"]]
        result = interrupt({"wait": "action_result", "request_id": s["request_id"]})
        entry = {"capability": step["capability"], "status": result.get("status"), "output": result.get("output") or {}, "error": result.get("error")}
        if step["capability"] == CapabilityId.DATA_PROFILE.value and result.get("status") == "success":
            entry["violations"] = profile_violations(entry["output"], step.get("expect") or {})
        update: dict[str, Any] = {"results": [*s.get("results", []), entry]}
        if result.get("status") not in ("success", "dry_run"):
            update.update(outcome="escalated", outcome_reason=f"{step['capability']} {result.get('status')}: {result.get('error') or ''}".strip())
        elif result.get("status") == "success" and step["capability"] in RECORDED_ACTIONS:
            self.d.store.record_action(
                self._run(s), RECORDED_ACTIONS[step["capability"]], s.get("plan_reason", ""), s["kind"],
                entry["output"].get("run_id"), False, self.d.clock(),
            )
        return update

    def wait_run(self, s: IncidentState) -> dict[str, Any]:
        finished = interrupt({"wait": "run_finished", "request_id": s["request_id"]})
        results = list(s["results"])
        results[-1] = {**results[-1], "run": {"run_id": finished.get("run_id"), "status": finished.get("status"), "url": finished.get("url")}}
        update: dict[str, Any] = {"results": results}
        fix = s.get("fix") or {}
        if (
            finished.get("status") != "succeeded" and results[-1]["capability"] == CapabilityId.TESTS_RUN.value
            and fix and fix["repairs"] < self.d.config.fix.repair_attempts
        ):
            failure = "\n".join(self.d.evidence.get_text(e) for e in finished.get("evidence_ids") or []) or f"test run {finished.get('run_id')} failed"
            update["fix"] = {**fix, "phase": "repair", "failure": failure, "failure_source": "Databricks test run"}
            self._audit(s, "tests_failed", run=finished.get("url"), repairs_left=self.d.config.fix.repair_attempts - fix["repairs"])
        elif finished.get("status") != "succeeded":
            update.update(outcome="escalated", outcome_reason=f"{results[-1]['capability']} run {finished.get('run_id')} ended {finished.get('status')}")
        return update

    def advance(self, s: IncidentState) -> dict[str, Any]:
        return {"step": s["step"] + 1, "request_id": None}

    def reconcile(self, s: IncidentState) -> dict[str, Any]:
        outcome, reason = verdict(s)
        self._audit(s, "reconciled", outcome=outcome, reason=reason)
        return {"outcome": outcome, "outcome_reason": reason}

    def close(self, s: IncidentState) -> dict[str, Any]:
        outcome, reason = s.get("outcome", "escalated"), s.get("outcome_reason", "")
        status = {"resolved": "resolved", "awaiting_code_fix": "open"}.get(outcome, "escalated")
        if s.get("incident_id"):
            self.d.store.update_incident(s["incident_id"], now=self.d.clock(), status=status)
        self.fix_closed(s, outcome, reason)
        self._audit(s, {"resolved": "incident_resolved", "awaiting_code_fix": "awaiting_code_fix"}.get(outcome, "incident_escalated"), outcome=outcome, reason=reason)
        severity = Severity.INFO if outcome in ("resolved", "awaiting_code_fix") else Severity.CRITICAL
        titles = {"resolved": "Resolved", "awaiting_code_fix": "Needs a code fix", "recommended": "Recommended action (a human acts)"}
        self._notify(s, f"{titles.get(outcome, 'Needs a human')}: {s['pipeline_name']}", reason, severity, outcome=outcome)
        with self.d.store.session() as session:
            row = session.get(WorkItemRow, s["work_item_id"])
            if row is not None:
                row.status = "done" if outcome == "resolved" else ("waiting_fix" if outcome == "awaiting_code_fix" else "escalated")
                row.incident_id, row.updated_at = s.get("incident_id"), self.d.clock()
                row.details = {**(row.details or {}), "outcome": outcome, "reason": reason, "wait": None}
                session.commit()
        return {}

    # routing --------------------------------------------------------------------
    @staticmethod
    def _after_plan(s: IncidentState) -> str:
        if s.get("steps"):
            return "decide"
        return "fix_write" if s["runbook"] == "code_fix" and not s.get("outcome") else "close"

    @staticmethod
    def _after_decide(s: IncidentState) -> str:
        mode = s["decision"]["mode"]
        if mode in ("execute", "dry_run"):
            return "submit"
        return "request_approval" if mode == "await_approval" else "close"

    @staticmethod
    def _after_approval(s: IncidentState) -> str:
        return "close" if s.get("outcome") else "decide"

    @staticmethod
    def _after_result(s: IncidentState) -> str:
        if s.get("outcome"):
            return "close"
        last, step = s["results"][-1], s["steps"][s["step"]]
        return "wait_run" if step.get("watch") and last["status"] == "success" and last["output"].get("run_id") else "advance"

    @staticmethod
    def _after_run(s: IncidentState) -> str:
        if s.get("outcome"):
            return "close"
        return "fix_repair" if (s.get("fix") or {}).get("phase") == "repair" else "advance"

    @staticmethod
    def _after_advance(s: IncidentState) -> str:
        if s["step"] < len(s["steps"]):
            return "decide"
        fix = s.get("fix")
        return "fix_next" if fix and fix["phase"] != "done" else "reconcile"

    @staticmethod
    def _closed_or(nxt: str):
        return lambda s: "close" if s.get("outcome") else nxt

    @staticmethod
    def _after_fix_next(s: IncidentState) -> str:
        if s["fix"]["phase"] == "pr":
            return "fix_open_pr"
        return "decide" if s.get("steps") else "reconcile"

    def _after_open_pr(self, s: IncidentState) -> str:
        return "fix_wait_merge" if s["fix"]["stage"] == len(self.d.config.fix.promotion) - 1 else "fix_wait_checks"

    @staticmethod
    def _after_checks(s: IncidentState) -> str:
        if s.get("outcome"):
            return "close"
        return {"repair": "fix_repair", "merge": "fix_merge"}[s["fix"]["phase"]]

    @staticmethod
    def _after_merge(s: IncidentState) -> str:
        if s.get("outcome"):
            return "close"
        return "fix_wait_merge" if s["fix"]["phase"] == "human_merge" else "fix_deploy"

    def build(self) -> StateGraph:
        g = StateGraph(IncidentState)
        for name in ("intake", "triage", "plan", "decide", "request_approval", "wait_approval", "submit", "wait_result", "wait_run",
                     "advance", "reconcile", "close", "fix_write", "fix_tests", "fix_repair", "fix_next", "fix_open_pr",
                     "fix_wait_checks", "fix_merge", "fix_wait_merge", "fix_deploy"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "intake")
        g.add_edge("intake", "triage")
        g.add_edge("triage", "plan")
        g.add_conditional_edges("plan", self._after_plan, ["decide", "fix_write", "close"])
        g.add_conditional_edges("decide", self._after_decide, ["submit", "request_approval", "close"])
        g.add_edge("request_approval", "wait_approval")
        g.add_conditional_edges("wait_approval", self._after_approval, ["decide", "close"])
        g.add_edge("submit", "wait_result")
        g.add_conditional_edges("wait_result", self._after_result, ["wait_run", "advance", "close"])
        g.add_conditional_edges("wait_run", self._after_run, ["advance", "fix_repair", "close"])
        g.add_conditional_edges("advance", self._after_advance, ["decide", "fix_next", "reconcile"])
        # code-fix path
        g.add_conditional_edges("fix_write", self._closed_or("fix_tests"), ["fix_tests", "close"])
        g.add_conditional_edges("fix_tests", self._closed_or("decide"), ["decide", "close"])
        g.add_conditional_edges("fix_repair", self._closed_or("decide"), ["decide", "close"])
        g.add_conditional_edges("fix_next", self._after_fix_next, ["fix_open_pr", "decide", "reconcile"])
        g.add_conditional_edges("fix_open_pr", self._after_open_pr, ["fix_wait_checks", "fix_wait_merge"])
        g.add_conditional_edges("fix_wait_checks", self._after_checks, ["fix_repair", "fix_merge", "close"])
        g.add_conditional_edges("fix_merge", self._after_merge, ["fix_wait_merge", "fix_deploy", "close"])
        g.add_conditional_edges("fix_wait_merge", self._closed_or("fix_deploy"), ["fix_deploy", "close"])
        g.add_edge("fix_deploy", "decide")
        g.add_edge("reconcile", "close")
        g.add_edge("close", END)
        return g

