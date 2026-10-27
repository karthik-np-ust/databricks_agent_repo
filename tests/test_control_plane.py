"""Control plane end to end: LangGraph incident graphs driving the real data plane over the simulator.

Both planes run in-process against one store; only signed requests and events pass between them.
Ports the legacy monitor scenarios (tests/test_monitor.py) to the redesigned flow.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from pipeline_agent.autonomy import PolicyBundle, PolicyEngine
from pipeline_agent.config import AgentConfig, PipelinePolicy, ReconcileCheck
from pipeline_agent.control_plane.deps import ControlPlaneDeps
from pipeline_agent.control_plane.registry import AgentRegistry
from pipeline_agent.control_plane.runner import ControlPlane
from pipeline_agent.data_plane.executor import Executor
from pipeline_agent.data_plane.observer import Observer
from pipeline_agent.data_plane.router import CapabilityRouter
from pipeline_agent.evidence import EvidenceStore, LocalBlobs
from pipeline_agent.kedb import KnownErrorDB
from pipeline_agent.llm.gateway import BudgetExceeded
from pipeline_agent.llm.triage import Diagnosis
from pipeline_agent.notify.webhook import ConsoleNotifier
from pipeline_agent.security.signing import Signer, Verifier
from pipeline_agent.sdk.models import ErrorClass
from pipeline_agent.store.db import ActionRequestRow, IncidentRow, StateStore, WorkItemRow
from pipeline_agent_simulator import SimulatorPlugin

CONFIG = Path(__file__).resolve().parents[1] / "config"
TOKEN = "dapiFAKE00000000000000000000000000"


class FakeTriage:
    def __init__(self, error: Exception | None = None):
        self.calls, self.error = 0, error

    def diagnose(self, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return Diagnosis(summary="Upstream quota", root_cause="quota", error_class=ErrorClass.INFRA, confidence=0.8,
                         suggested_fix="raise quota", model="fake")


class Demo:
    """Simulator + data plane + control plane, ticked together like the two real processes."""

    def __init__(self, clock, store, tmp_path, pipelines, policies=None, triage=None, checkpointer=None, **sim):
        self.clock, self.store = clock, store
        self.instance = SimulatorPlugin().build("sim", {"pipelines": pipelines, **sim}, {}, clock=clock)
        self.sim = self.instance.backend
        signer = Signer.generate()
        engine = PolicyEngine(PolicyBundle.load(CONFIG / "policy" / "autonomy.yaml"), AgentRegistry.load(CONFIG / "agents"))
        evidence = EvidenceStore(store, LocalBlobs(tmp_path))
        self.controls = {"enabled": True, "dry_run": False}
        config = AgentConfig(defaults=PipelinePolicy(max_duration_seconds=300), policies=policies or [])
        config.platforms = []  # plugin name lookup falls back to "*" (generic KEDB) + simulator rules below
        kedb = KnownErrorDB(store)
        kedb.seed_all()
        self.observer = Observer(config, {"sim": self.instance}, store, evidence, clock=clock)
        self.executor = Executor(store, CapabilityRouter({"sim": self.instance}), Verifier([signer.public_key]), engine, evidence,
                                 controls=lambda: self.controls, clock=clock)
        self.notifier = ConsoleNotifier()
        self.deps = ControlPlaneDeps(config=config, store=store, engine=engine, registry=AgentRegistry.load(CONFIG / "agents"),
                                     signer=signer, evidence=evidence, notifier=self.notifier, triage=triage,
                                     controls=lambda: self.controls, clock=clock)
        self.checkpointer = checkpointer or InMemorySaver()
        self.control = ControlPlane(self.deps, self.checkpointer, holder="cp", lease_ttl=timedelta(days=1))

    def cycle(self, seconds: float = 0):
        """observe -> decide -> execute -> decide (one poll of both processes)."""
        self.clock.advance(seconds)
        self.observer.tick()
        report = self.control.tick()
        self.executor.drain()
        report2 = self.control.tick()
        return report, report2

    def incident(self, pipeline: str) -> IncidentRow:
        with self.store.session() as s:
            return s.query(IncidentRow).filter(IncidentRow.pipeline_name == pipeline).order_by(IncidentRow.id).first()

    def incidents(self) -> list[IncidentRow]:
        with self.store.session() as s:
            return s.query(IncidentRow).all()

    def requests(self) -> list[ActionRequestRow]:
        with self.store.session() as s:
            return s.query(ActionRequestRow).order_by(ActionRequestRow.created_at).all()

    def audit_actions(self) -> list[str]:
        return [a.action for a in reversed(self.store.list_audit(500))]

    def item(self, pipeline: str) -> WorkItemRow:
        with self.store.session() as s:
            return s.query(WorkItemRow).filter(WorkItemRow.pipeline_name == pipeline).first()


TRANSIENT = {"name": "partner_feed_ingest", "behaviour": "fail_transient", "fail_attempts": 1, "duration_seconds": 30}


def test_transient_failure_in_prod_is_rerun_and_resolved(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [TRANSIENT])
    demo.cycle(31)
    [req] = demo.requests()
    assert req.capability == "pipeline.rerun" and req.level == 4 and req.status == "done"
    assert demo.item("partner_feed_ingest").status == "waiting"  # watching the rerun
    demo.cycle(31)
    inc = demo.incident("partner_feed_ingest")
    assert inc.status == "resolved" and inc.error_class == "transient"
    assert demo.item("partner_feed_ingest").details["outcome"] == "resolved"
    actions = demo.audit_actions()
    for expected in ("incident_opened", "kedb_matched", "planned", "policy_decision", "action_requested", "reconciled", "incident_resolved"):
        assert expected in actions
    assert len(demo.incidents()) == 1  # the rerun's own runs never open a second incident


def test_transient_failure_escalates_when_the_rerun_fails_again(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [{**TRANSIENT, "fail_attempts": 3}])
    demo.cycle(31)
    demo.cycle(31)
    inc = demo.incident("partner_feed_ingest")
    assert inc.status == "escalated"
    assert "ended failed" in demo.item("partner_feed_ingest").details["reason"]
    assert len(demo.incidents()) == 1 and len(demo.requests()) == 1


def test_code_failure_is_triaged_from_the_kedb_and_waits_for_a_code_fix(clock, store, tmp_path):
    policies = [PipelinePolicy(match="orders_pipeline", auto_fix=True)]
    demo = Demo(clock, store, tmp_path, [{"name": "orders_pipeline", "behaviour": "fail_code", "duration_seconds": 30},
                                         {"name": "other", "behaviour": "fail_code", "duration_seconds": 30}], policies=policies)
    demo.cycle(31)
    orders, other = demo.incident("orders_pipeline"), demo.incident("other")
    assert orders.status == "open" and orders.matched_rule == "cast_invalid_input" and orders.fix_eligible == 1
    assert other.fix_eligible == 0
    assert demo.item("orders_pipeline").status == "waiting_fix"
    assert demo.requests() == []


def test_permission_failure_escalates_without_any_action(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [{"name": "customer_export", "behaviour": "fail_permission", "duration_seconds": 30}])
    demo.cycle(31)
    assert demo.incident("customer_export").status == "escalated" and demo.requests() == []


def test_dev_transient_failure_is_recommend_only(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [{**TRANSIENT, "env": "dev"}])
    demo.cycle(31)
    assert demo.item("partner_feed_ingest").details["outcome"] == "recommended"
    assert demo.requests() == []


def test_slow_run_needs_one_console_approval_for_cancel_and_rerun(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [{"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 1800}])
    demo.cycle(301)
    [pending] = demo.deps.approvals.list()
    assert pending.capability == "pipeline.cancel" and demo.requests() == []
    assert any("Approval needed" in n.title for n in demo.notifier.sent)

    demo.deps.approvals.decide(pending.id, True, by="user:karthik")
    demo.cycle(1)  # cancel executes; the rerun is decided with the same approval and submitted
    demo.cycle(1)
    caps = [r.capability for r in demo.requests()]
    assert caps == ["pipeline.cancel", "pipeline.rerun"]
    assert all(r.payload["approval"]["kind"] == "console" for r in demo.requests())
    assert demo.deps.approvals.list() == []  # no second approval for the rerun
    demo.cycle(1801)
    assert demo.incident("sales_report").status == "resolved"


def test_rejected_approval_escalates_and_executes_nothing(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [{"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 1800}])
    demo.cycle(301)
    [pending] = demo.deps.approvals.list()
    demo.deps.approvals.decide(pending.id, False, by="user:karthik", comment="month-end run, let it finish")
    demo.cycle(1)
    assert demo.incident("sales_report").status == "escalated" and demo.requests() == []
    assert "month-end" in demo.item("sales_report").details["reason"]


def test_unanswered_approval_times_out(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [{"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 100000}])
    demo.cycle(301)
    demo.cycle(25 * 3600)
    assert demo.incident("sales_report").status == "escalated"
    assert "timed out" in demo.item("sales_report").details["reason"]


def test_kill_switch_and_dry_run(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [TRANSIENT])
    demo.controls["enabled"] = False
    demo.cycle(31)
    assert demo.incident("partner_feed_ingest").status == "escalated" and demo.requests() == []

    demo2 = Demo(clock, StateStore("sqlite://"), tmp_path / "2", [TRANSIENT])
    demo2.controls["dry_run"] = True
    demo2.cycle(31)
    [req] = demo2.requests()
    assert req.status == "done" and len(demo2.sim.runs) == 1  # requested, checked, not executed
    assert demo2.item("partner_feed_ingest").details["outcome"] == "recommended"


def test_reconciliation_checks_data_after_a_successful_rerun(clock, store, tmp_path):
    checks = [ReconcileCheck(table="{env}.silver.orders", keys=["order_id"], not_null=["store_id"])]
    policies = [PipelinePolicy(match="partner_feed_ingest", reconcile=checks)]
    demo = Demo(clock, store, tmp_path, [TRANSIENT], policies=policies,
                profiles={"prod.silver.orders": {"rows": 50, "duplicate_keys": 2, "nulls": {"store_id": 0}}})
    demo.cycle(31)
    demo.cycle(31)
    caps = [r.capability for r in demo.requests()]
    assert caps == ["pipeline.rerun", "data.profile"]
    assert demo.incident("partner_feed_ingest").status == "escalated"
    assert "2 duplicate key row(s)" in demo.item("partner_feed_ingest").details["reason"]


def test_restart_resumes_the_thread_without_resubmitting(clock, store, tmp_path):
    saver = InMemorySaver()
    demo = Demo(clock, store, tmp_path, [TRANSIENT], checkpointer=saver)
    demo.clock.advance(31)
    demo.observer.tick()
    demo.control.tick()  # request submitted, thread waits for the result
    assert len(demo.requests()) == 1
    restarted = ControlPlane(demo.deps, saver, holder="cp")  # same checkpoints, new process
    demo.executor.drain()
    restarted.tick()
    restarted.tick()
    assert len(demo.requests()) == 1  # resumed, not replayed from scratch
    demo.clock.advance(31)
    demo.observer.tick()
    restarted.tick()
    assert demo.incident("partner_feed_ingest").status == "resolved"


def test_secrets_never_reach_incidents_or_notifications(clock, store, tmp_path):
    error = f"[PERMISSION_DENIED] token {TOKEN} rejected"
    demo = Demo(clock, store, tmp_path, [{"name": "customer_export", "behaviour": "fail_permission", "duration_seconds": 30, "error_message": error}])
    demo.cycle(31)
    assert TOKEN not in demo.incident("customer_export").error_message
    assert all(TOKEN not in f"{n.title}{n.summary}{n.facts}" for n in demo.notifier.sent)


def test_llm_only_when_the_kedb_has_no_answer_and_budget_stop_escalates(clock, store, tmp_path):
    unknown = {"name": "mystery", "behaviour": "fail_code", "duration_seconds": 30, "error_message": "Something odd happened"}
    triage = FakeTriage()
    demo = Demo(clock, store, tmp_path, [TRANSIENT, unknown], triage=triage)
    demo.cycle(31)
    assert triage.calls == 1  # the transient failure matched the KEDB: no LLM call
    inc = demo.incident("mystery")
    assert inc.diagnosis["summary"] == "Upstream quota" and inc.status == "escalated"

    stop = FakeTriage(BudgetExceeded("daily request budget reached"))
    demo2 = Demo(clock, StateStore("sqlite://"), tmp_path / "2", [unknown], triage=stop)
    demo2.cycle(31)
    assert demo2.incident("mystery").status == "escalated" and "llm_budget_stop" in demo2.audit_actions()


def test_only_one_control_plane_is_active(clock, store, tmp_path):
    demo = Demo(clock, store, tmp_path, [TRANSIENT])
    other = ControlPlane(demo.deps, InMemorySaver(), holder="other")
    assert demo.control.tick().leader and not other.tick().leader
