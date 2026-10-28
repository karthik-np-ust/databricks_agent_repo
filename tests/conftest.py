from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline_agent.store.db import StateStore


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
