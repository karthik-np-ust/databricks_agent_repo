"""Data plane end to end over the simulator: observer events, executor checks, ledger, watches."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import update

from pipeline_agent.autonomy import PolicyBundle, PolicyEngine
from pipeline_agent.boundary import ActionQueue, ActionRequest, Approval, CapabilityId, SignedActionRequest
from pipeline_agent.boundary.events import EventLog
from pipeline_agent.config import AgentConfig, PipelinePolicy
from pipeline_agent.control_plane.registry import AgentRegistry
from pipeline_agent.data_plane.executor import Executor
from pipeline_agent.data_plane.observer import Observer
from pipeline_agent.data_plane.router import CapabilityRouter
from pipeline_agent.data_plane.service import DataPlane
from pipeline_agent.evidence import EvidenceStore, LocalBlobs
from pipeline_agent.leases import Leases
from pipeline_agent.security.signing import Signer, Verifier
from pipeline_agent.store.db import ActionRequestRow, LedgerRow
from pipeline_agent_simulator import SimulatorPlugin

CONFIG = Path(__file__).resolve().parents[1] / "config"
TOKEN = "dapiFAKE00000000000000000000000000"

PIPELINES = [
    {"name": "orders_pipeline", "behaviour": "fail_code", "duration_seconds": 30},
    {"name": "partner_feed_ingest", "behaviour": "fail_transient", "fail_attempts": 1, "duration_seconds": 30,
     "error_message": f"ConnectionError: Connection reset by peer (HTTP 503) token {TOKEN}"},
    {"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 1800},
    {"name": "dev_orders", "behaviour": "fail_code", "duration_seconds": 30, "env": "dev"},
]


class Plane:
    def __init__(self, clock, store, tmp_path, **sim):
        self.clock, self.store = clock, store
        self.instance = SimulatorPlugin().build("sim", {"pipelines": PIPELINES, **sim}, {}, clock=clock)
        self.sim = self.instance.backend
        self.signer = Signer.generate()
        self.engine = PolicyEngine(PolicyBundle.load(CONFIG / "policy" / "autonomy.yaml"), AgentRegistry.load(CONFIG / "agents"))
        self.evidence = EvidenceStore(store, LocalBlobs(tmp_path))
        self.controls = {"enabled": True, "dry_run": False}
        self.config = AgentConfig(defaults=PipelinePolicy(max_duration_seconds=300))
        self.observer = Observer(self.config, {"sim": self.instance}, store, self.evidence, clock=clock)
        self.executor = Executor(
            store, CapabilityRouter({"sim": self.instance}), Verifier([self.signer.public_key]), self.engine, self.evidence,
            controls=lambda: self.controls, clock=clock,
        )
        self.queue = ActionQueue(store)
        self.events = EventLog(store)

    def request(self, capability, target, *, env="prod", agent="recovery", key=None, approval=None, pattern=None, **params):
        return ActionRequest.new(
            idempotency_key=key or f"inc-1:{capability}:{sorted(target.items())}", capability=capability, platform_id="sim", env=env,
            target=target, params=params, agent=agent, incident_id=1, level=4, policy_bundle=self.engine.bundle.sha256,
            approval=approval or Approval(kind="policy", ref="test"), pattern=pattern, issued_at=self.clock(),
        )

    def run(self, req, signer=None):
        self.queue.submit(SignedActionRequest.sign(req, signer or self.signer))
        return self.executor.process_one()

    def run_of(self, name):
        return next(r for r in self.sim.list_runs(self.clock() - __import__("datetime").timedelta(days=1)) if r.pipeline_name == name)

    def kinds(self, kind):
        return [e for e in self.events.list(kind=kind)]


@pytest.fixture
def plane(clock, store, tmp_path):
    return Plane(clock, store, tmp_path)


def finish_boot_runs(plane):
    plane.clock.advance(31)
    return plane.observer.tick()


# --- observer -------------------------------------------------------------------------


def test_observer_reports_each_failure_once_with_redacted_evidence(plane):
    report = finish_boot_runs(plane)
    failed = {key for kind, key in report.events if kind == "run_failed"}
    assert failed == {plane.run_of(n).key for n in ("orders_pipeline", "partner_feed_ingest", "dev_orders")}
    event = next(e for e in plane.kinds("run_failed") if "partner" in e.run_key)
    assert event.payload["error_class"] == "transient" and TOKEN not in event.payload["error_headline"]
    assert TOKEN not in plane.evidence.get_text(event.evidence_ids[0])
    assert plane.observer.tick().events == []  # nothing new


def test_observer_flags_a_slow_run_once(plane):
    plane.clock.advance(301)
    report = plane.observer.tick()
    assert ("run_slow", plane.run_of("sales_report").key) in report.events
    plane.clock.advance(60)
    assert not [e for e in plane.observer.tick().events if e[0] == "run_slow"]


# --- executor: happy paths ---------------------------------------------------------------


def test_transient_rerun_executes_once_and_is_watched_to_completion(plane):
    finish_boot_runs(plane)
    failed = plane.run_of("partner_feed_ingest")
    req = plane.request(CapabilityId.RERUN, {"pipeline_id": failed.pipeline_id, "run_id": failed.run_id}, pattern="transient")
    result = plane.run(req)
    assert result.status == "success", result.error
    new_run_id = result.output["run_id"]
    assert new_run_id != failed.run_id and "run_failed" not in result.output
    with plane.store.session() as s:
        assert s.get(LedgerRow, req.idempotency_key).state == "SUCCESS"
    assert plane.evidence.meta(result.evidence_ids[0]).kind == "action_output"

    plane.clock.advance(31)
    report = plane.observer.tick()
    finished = [e for e in plane.kinds("run_finished") if e.payload["run_id"] == new_run_id]
    assert finished and finished[0].payload["status"] == "succeeded" and finished[0].payload["request_id"] == req.id
    assert ("run_finished", finished[0].run_key) in report.events


def test_code_fix_path_tests_sync_and_prod_repair(clock, store, tmp_path):
    plane = Plane(clock, store, tmp_path, test_outcomes={"fix/bad": "fail"}, branch_heads={"prod": "abc1234def"})
    finish_boot_runs(plane)
    failed = plane.run_of("orders_pipeline")

    bad = plane.run(plane.request(CapabilityId.TESTS_RUN, {"pipeline_id": "orders_pipeline"}, agent="testing", env="dev",
                                  branch="fix/bad", notebooks=["tests/test_transform_silver"]))
    good = plane.run(plane.request(CapabilityId.TESTS_RUN, {"pipeline_id": "orders_pipeline"}, agent="testing", env="dev", key="t2",
                                   branch="fix/good", notebooks=["tests/test_transform_silver"]))
    assert bad.status == good.status == "success"
    plane.clock.advance(121)
    plane.observer.tick()
    outcomes = {e.payload["run_id"]: e for e in plane.kinds("run_finished")}
    assert outcomes[bad.output["run_id"]].payload["status"] == "failed"
    assert "AssertionError" in plane.evidence.get_text(outcomes[bad.output["run_id"]].evidence_ids[0])
    assert outcomes[good.output["run_id"]].payload["status"] == "succeeded"

    merged = Approval(kind="github_merge", ref="https://github.com/org/repo/pull/12")
    sync = plane.run(plane.request(CapabilityId.CODE_SYNC, {"pipeline_id": "orders_pipeline"}, approval=merged, pattern="code", expected_head="abc1234"))
    assert sync.status == "success" and sync.output["head"] == "abc1234def"
    repair = plane.run(plane.request(CapabilityId.REPAIR, {"pipeline_id": "orders_pipeline", "run_id": failed.run_id}, approval=merged, pattern="code"))
    assert repair.status == "success", repair.error
    assert plane.observer.tick().events == []  # repair accepted, not finished: old failure isn't re-reported
    plane.clock.advance(31)
    plane.observer.tick()
    done = [e for e in plane.kinds("run_finished") if e.payload["run_id"] == failed.run_id]
    assert done and done[0].payload["status"] == "succeeded"


def test_read_only_profile_for_reconciliation(plane):
    result = plane.run(plane.request(CapabilityId.DATA_PROFILE, {"table": "prod.silver.orders"}, agent="reconciliation",
                                     keys=["order_id"], not_null=["store_id"]))
    assert result.status == "success" and result.output["rows"] == 100 and result.output["nulls"] == {"store_id": 0}


# --- executor: refusals -------------------------------------------------------------------


def test_replayed_key_returns_the_recorded_outcome_without_executing(plane):
    finish_boot_runs(plane)
    failed = plane.run_of("partner_feed_ingest")
    target = {"pipeline_id": failed.pipeline_id, "run_id": failed.run_id}
    first = plane.run(plane.request(CapabilityId.RERUN, target, pattern="transient", key="inc-1:rerun:1"))
    runs_before = len(plane.sim.runs)
    # Same idempotency key, new request id: the queue dedups, and the ledger would too.
    replay_id, created = plane.queue.submit(SignedActionRequest.sign(plane.request(CapabilityId.RERUN, target, pattern="transient", key="inc-1:rerun:1"), plane.signer))
    assert not created and replay_id == first.request_id
    with plane.store.session() as s:  # force the executor to see it again, as after a crash between claim and complete
        s.execute(update(ActionRequestRow).where(ActionRequestRow.id == replay_id).values(status="queued"))
        s.commit()
    again = plane.executor.process_one()
    assert again.status == "success" and again.output["duplicate_of"] == "inc-1:rerun:1"
    assert len(plane.sim.runs) == runs_before


def test_tampered_untrusted_and_drifted_requests_are_rejected(plane):
    finish_boot_runs(plane)
    failed = plane.run_of("partner_feed_ingest")
    target = {"pipeline_id": failed.pipeline_id, "run_id": failed.run_id}

    request_id, _ = plane.queue.submit(SignedActionRequest.sign(plane.request(CapabilityId.RERUN, target, pattern="transient", key="k1"), plane.signer))
    row = plane.queue.get(request_id)
    with plane.store.session() as s:  # someone edits the queued request in the DB
        s.execute(update(ActionRequestRow).where(ActionRequestRow.id == request_id).values(payload={**row.payload, "env": "dev"}))
        s.commit()
    assert "signature" in plane.executor.process_one().error

    untrusted = plane.run(plane.request(CapabilityId.RERUN, target, pattern="transient", key="k2"), signer=Signer.generate())
    assert "unknown signing key" in untrusted.error

    drifted = plane.request(CapabilityId.RERUN, target, pattern="transient", key="k3")
    drifted.policy_bundle = "0" * 64
    assert "policy drift" in plane.run(drifted).error
    assert len(plane.sim.runs) == 4  # nothing ran


def test_policy_is_rechecked_in_the_data_plane(plane):
    plane.clock.advance(10)
    slow = plane.run_of("sales_report")
    target = {"pipeline_id": slow.pipeline_id, "run_id": slow.run_id}
    no_approval = plane.run(plane.request(CapabilityId.CANCEL, target, pattern="slow", key="c1"))
    assert no_approval.status == "rejected" and "needs approval from console" in no_approval.error
    approved = plane.run(plane.request(CapabilityId.CANCEL, target, pattern="slow", key="c2", approval=Approval(kind="console", ref="approval:7", by="karthik")))
    assert approved.status == "success"

    plane.controls["enabled"] = False
    off = plane.run(plane.request(CapabilityId.CANCEL, target, pattern="slow", key="c3", approval=Approval(kind="console", ref="approval:8")))
    assert off.status == "rejected" and off.output["mode"] == "manual"


def test_preconditions_use_live_state(plane):
    finish_boot_runs(plane)
    failed = plane.run_of("partner_feed_ingest")
    target = {"pipeline_id": failed.pipeline_id, "run_id": failed.run_id}
    plane.run(plane.request(CapabilityId.RERUN, target, pattern="transient", key="r1"))
    plane.clock.advance(31)  # the rerun succeeds
    stale = plane.run(plane.request(CapabilityId.RERUN, target, pattern="transient", key="r2"))
    assert stale.status == "rejected" and "newer run" in stale.error

    smoke_prod = plane.run(plane.request(CapabilityId.SMOKE_RUN, {"pipeline_id": "orders_pipeline"}, key="s0"))
    assert smoke_prod.status == "rejected" and "policy" in smoke_prod.error  # the policy alone already refuses prod
    mislabelled = plane.run(plane.request(CapabilityId.SMOKE_RUN, {"pipeline_id": "orders_pipeline"}, env="dev", key="s1"))
    assert mislabelled.status == "rejected" and "dev/qa only" in mislabelled.error  # live state catches a wrong env label
    smoke_dev = plane.run(plane.request(CapabilityId.SMOKE_RUN, {"pipeline_id": "dev_orders"}, env="dev", key="s2"))
    assert smoke_dev.status == "success"

    unknown_run = plane.run(plane.request(CapabilityId.REPAIR, {"pipeline_id": "orders_pipeline", "run_id": "999"}, env="dev", key="x1"))
    assert unknown_run.status == "rejected" and "precondition check failed" in unknown_run.error

    merge = plane.run(plane.request(CapabilityId.CODE_MERGE, {"pipeline_id": "dev_orders"}, env="dev", key="m1"))
    assert merge.status == "rejected" and "not a data-plane capability" in merge.error


def test_dry_run_checks_everything_but_executes_nothing(plane):
    finish_boot_runs(plane)
    failed = plane.run_of("partner_feed_ingest")
    plane.controls["dry_run"] = True
    result = plane.run(plane.request(CapabilityId.RERUN, {"pipeline_id": failed.pipeline_id, "run_id": failed.run_id}, pattern="transient"))
    assert result.status == "dry_run" and "no_newer_success" in result.output["preconditions"]
    assert len(plane.sim.runs) == 4
    with plane.store.session() as s:
        assert s.query(LedgerRow).count() == 0


def test_platform_error_fails_the_ledger_entry_and_a_bad_sync_is_unknown(plane, monkeypatch):
    finish_boot_runs(plane)
    failed = plane.run_of("dev_orders")

    def boom(run):
        raise RuntimeError(f"INTERNAL_ERROR token {TOKEN}")

    monkeypatch.setattr(plane.sim, "repair", boom)
    result = plane.run(plane.request(CapabilityId.REPAIR, {"pipeline_id": "dev_orders", "run_id": failed.run_id}, env="dev", key="p1"))
    assert result.status == "failed" and TOKEN not in result.error
    with plane.store.session() as s:
        assert s.get(LedgerRow, "p1").state == "FAILED"

    wrong_head = plane.run(plane.request(CapabilityId.CODE_SYNC, {"pipeline_id": "dev_orders"}, env="dev", key="y1", pattern="code", expected_head="ffff000"))
    assert wrong_head.status == "unknown" and "not the merged commit" in wrong_head.error
    with plane.store.session() as s:
        assert s.get(LedgerRow, "y1").state == "UNKNOWN"


def test_only_one_data_plane_is_active(plane, store, clock):
    a = DataPlane(plane.observer, plane.executor, Leases(store), holder="a", clock=clock)
    b = DataPlane(plane.observer, plane.executor, Leases(store), holder="b", clock=clock)
    assert a.tick().leader and not b.tick().leader
    a.stop()
    assert b.tick().leader
