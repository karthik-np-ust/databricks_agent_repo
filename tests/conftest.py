from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline_agent.config import AgentConfig, PipelinePolicy, Settings
from pipeline_agent.monitor import Monitor
from pipeline_agent.notify.webhook import ConsoleNotifier
from pipeline_agent.store.db import StateStore
from pipeline_agent_simulator import SimulatorPlugin


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Tests never see AGENT_* settings from the developer's environment or .env."""
    import os

    for key in list(os.environ):
        if key.startswith("AGENT_"):
            monkeypatch.delenv(key)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store() -> StateStore:
    return StateStore("sqlite://")  # in-memory


@pytest.fixture
def make_monitor(clock, store):
    """Build a Monitor over one simulator instance with the given pipelines/policies."""

    def _make(pipelines, policies=None, defaults=None, settings=None, circuit_breaker=None, start_on_boot=True):
        config = AgentConfig(
            defaults=defaults or PipelinePolicy(max_duration_seconds=300),
            policies=policies or [],
            **({"circuit_breaker": circuit_breaker} if circuit_breaker else {}),
        )
        instance = SimulatorPlugin().build(
            "sim", {"pipelines": pipelines, "start_on_boot": start_on_boot}, {}, clock=clock
        )
        notifier = ConsoleNotifier()
        monitor = Monitor(
            settings or Settings(_env_file=None),
            config,
            {"sim": instance},
            store,
            notifier,
            clock=clock,
        )
        return monitor, instance.backend, notifier

    return _make
