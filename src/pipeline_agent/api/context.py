"""Shared state for the console API: config, store, GitHub client, caches.

The console holds no platform credentials: platform state comes from the observer's snapshots."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from ..config import AgentConfig, Settings, load_agent_config
from ..git.github import GitHubClient
from ..store.db import StateStore
from .auth import TokenSigner

log = logging.getLogger(__name__)


class TTLCache:
    """Small thread-safe cache so a dashboard refresh doesn't hammer Databricks or GitHub."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, produce: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = produce()
        with self._lock:
            self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


class ConsoleContext:
    def __init__(self, settings: Settings, config: AgentConfig | None = None, store: StateStore | None = None,
                 github: GitHubClient | None = None):
        self.settings = settings
        self.config = config or load_agent_config(settings.config_dir)
        self.store = store or StateStore(settings.database_url)
        self.signer = TokenSigner(settings.console_secret.get_secret_value() if settings.console_secret else None)
        self.cache = TTLCache()
        self._github = github
        self._github_error: str | None = None
        self._triage = None
        self._lock = threading.Lock()

    @property
    def github(self) -> GitHubClient | None:
        with self._lock:
            if self._github is None and self._github_error is None:
                try:
                    from ..runtime import build_github

                    self._github = build_github(self.settings, self.config)
                except Exception as exc:  # not configured: console still works, fix diffs just aren't shown
                    self._github_error = str(exc)
            return self._github

    # Control-plane pieces the console reads and writes. The console never calls a platform to
    # change it: actions become events the control plane turns into signed requests.
    @property
    def events(self):
        from ..boundary.events import EventLog

        return EventLog(self.store)

    @property
    def approvals(self):
        from ..control_plane.approvals import Approvals

        return Approvals(self.store)

    @property
    def kedb(self):
        from ..kedb import KnownErrorDB

        return KnownErrorDB(self.store)

    @property
    def evidence(self):
        from ..evidence import EvidenceStore, LocalBlobs

        return EvidenceStore(self.store, LocalBlobs(self.settings.evidence_dir))

    @property
    def registry(self):
        from ..control_plane.registry import AgentRegistry

        return AgentRegistry.load(self.settings.config_dir / "agents")

    @property
    def policy_bundle(self):
        from ..autonomy import PolicyBundle

        return PolicyBundle.load(self.settings.config_dir / "policy" / "autonomy.yaml")

    def triage(self):
        """LLM triage for 'Re-run diagnosis' (the console holds the LLM key, never a platform key)."""
        with self._lock:
            if self._triage is None:
                from ..llm.triage import Triage
                from ..runtime import build_gateway

                self._triage = Triage(build_gateway(self.settings, self.config, self.store))
            return self._triage
