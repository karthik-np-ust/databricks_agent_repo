"""End-to-end behaviour of monitor + policy against the simulator (fault scenarios 1, 2, 3, 11)."""

from __future__ import annotations

from pipeline_agent.config import CircuitBreaker, PipelinePolicy, Settings
from pipeline_agent.sdk import PlatformInstance


def actions(report):
    return [a for _, a, _ in report.decisions]


def test_healthy_run_needs_no_action(make_monitor, clock, store):
    monitor, _, notifier = make_monitor([{"name": "orders", "duration_seconds": 60}])
    assert actions(monitor.tick()) == []
    clock.advance(120)
    assert actions(monitor.tick()) == []
    assert notifier.sent == []
    assert store.list_incidents() == []


def test_slow_run_is_cancelled_and_rerun_then_escalated_on_third_breach(make_monitor, clock, store):
    """Scenario 1: cap of 2 reruns/day, third breach escalates."""
    monitor, sim, notifier = make_monitor([{"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 900}])
    monitor.tick()
    clock.advance(301)
    assert actions(monitor.tick()) == ["cancel_and_rerun"]
    clock.advance(301)
    assert actions(monitor.tick()) == ["cancel_and_rerun"]
    clock.advance(301)
    assert actions(monitor.tick()) == ["escalate"]
    clock.advance(60)
    assert actions(monitor.tick()) == [], "escalation happens once per run"

    statuses = [r["cancelled_at"] is not None for r in sim.runs]
    assert statuses == [True, True, False], "only the agent's two reruns were cancelled"
    assert [r["triggered_by"] for r in sim.runs] == ["schedule", "agent", "agent"]
    assert notifier.sent[-1].severity == "critical"


def test_transient_failure_is_rerun_once_and_recovers(make_monitor, clock, store):
    """Scenario 2: fails once, rerun succeeds, no incident and no PR."""
    monitor, sim, _ = make_monitor([{"name": "partner_feed", "behaviour": "fail_transient", "fail_attempts": 1}])
    monitor.tick()
    clock.advance(61)
    assert actions(monitor.tick()) == ["rerun"]
    clock.advance(61)
    assert actions(monitor.tick()) == []
    assert store.list_incidents() == []
    assert len(sim.runs) == 2


def test_transient_failure_escalates_after_retries(make_monitor, clock, store):
    monitor, _, _ = make_monitor([{"name": "partner_feed", "behaviour": "fail_transient", "fail_attempts": 5}])
    monitor.tick()
    clock.advance(61)
    assert actions(monitor.tick()) == ["rerun"]
    clock.advance(61)
    assert actions(monitor.tick()) == ["escalate"]
    [incident] = store.list_incidents()
    assert incident.status == "escalated" and incident.error_class == "transient"


def test_code_failure_opens_incident_and_is_fix_eligible_only_with_auto_fix(make_monitor, clock, store):
    """Scenario 3 (detection part): code failure -> incident, no rerun."""
    monitor, sim, notifier = make_monitor(
        [{"name": "orders_pipeline", "behaviour": "fail_code", "code_path": "sample_pipelines/src"}],
        policies=[PipelinePolicy(match="orders_*", auto_fix=True)],
    )
    monitor.tick()
    clock.advance(61)
    assert actions(monitor.tick()) == ["open_incident"]
    [incident] = store.list_incidents()
    assert incident.status == "open"
    assert incident.error_class == "code"
    assert incident.matched_rule == "cast_invalid_input"
    assert incident.fix_eligible == 1
    assert len(sim.runs) == 1, "code failures are never blindly rerun"
    clock.advance(60)
    assert actions(monitor.tick()) == [], "incident is opened once"


def test_permission_failure_escalates_without_rerun(make_monitor, clock, store):
    """Scenario 11."""
    monitor, sim, _ = make_monitor([{"name": "orders_pipeline", "behaviour": "fail_permission"}])
    monitor.tick()
    clock.advance(61)
    assert actions(monitor.tick()) == ["escalate"]
    [incident] = store.list_incidents()
    assert incident.error_class == "infra" and incident.fix_eligible == 0
    assert len(sim.runs) == 1


def test_dev_env_is_notify_only(make_monitor, clock):
    monitor, sim, notifier = make_monitor([{"name": "sales_report", "env": "dev", "behaviour": "slow"}])
    monitor.tick()
    clock.advance(301)
    assert actions(monitor.tick()) == ["notify"]
    assert all(r["cancelled_at"] is None for r in sim.runs)


def test_secret_in_error_is_redacted_everywhere(make_monitor, clock, store):
    """Scenario 13."""
    fake = "dapiFAKE0123456789abcdef0123456789"
    monitor, _, notifier = make_monitor(
        [{"name": "partner_feed", "behaviour": "fail_code", "error_message": f"AttributeError: token {fake} rejected"}]
    )
    monitor.tick()
    clock.advance(61)
    monitor.tick()
    [incident] = store.list_incidents()
    assert fake not in incident.error_message and "[REDACTED:DATABRICKS_TOKEN]" in incident.error_message
    assert all(fake not in n.summary and fake not in str(n.facts) for n in notifier.sent)
    assert all(fake not in str(row.details) for row in store.list_audit())


def test_kill_switch_skips_everything(make_monitor, clock, store):
    monitor, sim, _ = make_monitor(
        [{"name": "sales_report", "behaviour": "slow"}], settings=Settings(_env_file=None, enabled=False)
    )
    clock.advance(400)
    report = monitor.tick()
    assert report.skipped and report.decisions == []
    assert len(sim.runs) == 1


def test_dry_run_records_but_does_not_act(make_monitor, clock, store):
    monitor, sim, notifier = make_monitor(
        [{"name": "sales_report", "behaviour": "slow"}], settings=Settings(_env_file=None, dry_run=True)
    )
    monitor.tick()
    clock.advance(301)
    assert actions(monitor.tick()) == ["cancel_and_rerun"]
    assert len(sim.runs) == 1 and sim.runs[0]["cancelled_at"] is None
    assert "DRY RUN" in notifier.sent[-1].facts["mode"]


def test_circuit_breaker_stops_automatic_actions(make_monitor, clock):
    pipelines = [{"name": f"job{i}", "behaviour": "slow"} for i in range(3)]
    monitor, _, _ = make_monitor(pipelines, circuit_breaker=CircuitBreaker(max_actions_per_hour=2))
    monitor.tick()
    clock.advance(301)
    assert sorted(actions(monitor.tick())) == ["cancel_and_rerun", "cancel_and_rerun", "escalate"]


def test_baseline_threshold_used_when_no_explicit_threshold(make_monitor, clock):
    monitor, sim, _ = make_monitor(
        [{"name": "orders", "duration_seconds": 100}],
        defaults=PipelinePolicy(baseline_min_samples=3, baseline_multiplier=1.5),
    )
    for _ in range(3):  # build history of 100s runs
        monitor.tick()
        clock.advance(101)
        monitor.tick()
        sim.trigger("orders", "schedule")
    sim._by_name["orders"].duration_seconds = 1000  # this run is slow
    sim.trigger("orders", "schedule")
    monitor.tick()
    clock.advance(140)
    assert actions(monitor.tick()) == [], "140s is under p95(100s) * 1.5"
    clock.advance(20)
    assert "cancel_and_rerun" in actions(monitor.tick())


def test_platform_without_controller_escalates_threshold(make_monitor, clock, store):
    monitor, sim, _ = make_monitor([{"name": "sales_report", "behaviour": "slow"}])
    inst = monitor.instances["sim"]
    monitor.instances["sim"] = PlatformInstance("sim", inst.manifest, run_source=sim, logs=inst.logs)
    monitor.tick()
    clock.advance(301)
    assert actions(monitor.tick()) == ["escalate"]
    assert sim.runs[0]["cancelled_at"] is None


def test_broken_platform_does_not_stop_others(make_monitor, clock):
    monitor, sim, _ = make_monitor([{"name": "orders", "behaviour": "fail_code"}])

    class Broken:
        def list_pipelines(self):
            raise ConnectionError("workspace unreachable")

        def list_runs(self, since, active_only=False):
            return []

    inst = monitor.instances["sim"]
    monitor.instances = {"broken": PlatformInstance("broken", inst.manifest, run_source=Broken()), "sim": inst}
    monitor._classifiers["broken"] = monitor._classifiers["sim"]
    clock.advance(61)
    report = monitor.tick()
    assert report.errors and "broken" in report.errors[0]
    assert actions(report) == ["open_incident"]
