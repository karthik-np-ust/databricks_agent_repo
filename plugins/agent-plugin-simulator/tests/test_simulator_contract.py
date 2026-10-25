from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline_agent.sdk import RunStatus
from pipeline_agent.testing import PluginContractSuite
from pipeline_agent_simulator import SimulatorPlugin

T0 = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


PIPELINES = [
    {"name": "ok_job", "duration_seconds": 30},
    {"name": "broken_job", "behaviour": "fail_code", "duration_seconds": 30},
]


class TestSimulatorContract(PluginContractSuite):
    @pytest.fixture
    def plugin(self):
        return SimulatorPlugin()

    @pytest.fixture
    def clock(self):
        return Clock()

    @pytest.fixture
    def instance(self, plugin, clock):
        inst = plugin.build("sim", {"pipelines": PIPELINES}, {}, clock=clock)
        clock.now = T0 + timedelta(seconds=60)  # boot runs have finished
        return inst

    @pytest.fixture
    def failed_run(self, instance):
        return next(r for r in instance.run_source.list_runs(T0) if r.status == RunStatus.FAILED)


def test_state_file_persists_runs(tmp_path):
    state = tmp_path / "sim.json"
    cfg = {"pipelines": PIPELINES, "state_file": str(state)}
    first = SimulatorPlugin().build("sim", cfg, {})
    assert len(first.backend.runs) == 2
    second = SimulatorPlugin().build("sim", cfg, {})
    assert len(second.backend.runs) == 2, "boot runs are not re-created when state exists"


def test_cancel_marks_run_cancelled():
    clock = Clock()
    inst = SimulatorPlugin().build("sim", {"pipelines": [{"name": "slow", "behaviour": "slow"}]}, {}, clock=clock)
    [run] = inst.run_source.list_runs(T0, active_only=True)
    inst.controller.cancel(run)
    [after] = inst.run_source.list_runs(T0)
    assert after.status == RunStatus.CANCELLED


def test_costs_are_estimated_from_duration():
    clock = Clock()
    inst = SimulatorPlugin().build("sim", {"pipelines": [{"name": "j", "duration_seconds": 3600, "dbu_per_hour": 2}]}, {}, clock=clock)
    clock.now = T0 + timedelta(hours=2)
    [rec] = inst.costs.costs(T0)
    assert rec.quantity == 2.0 and rec.is_estimate
