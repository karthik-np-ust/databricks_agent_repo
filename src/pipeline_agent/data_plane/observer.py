"""Observer: polls the platforms and reports what it sees as structured, redacted events.

    run_failed    a run failed (once per run), with its failure text in the evidence store
    run_slow      an active run passed its threshold (explicit, or p95 baseline x multiplier)
    run_finished  a run the executor started (rerun, repair, smoke, tests) reached a terminal state

It decides nothing: classification hints (error class, matched rule) are passed along, the
control plane's agents decide what to do. Watermarks are kept per platform under
``observer:<platform>`` so it never shares state with the legacy monitor.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from ..boundary.events import EventLog
from ..config import AgentConfig
from ..evidence import EvidenceStore
from ..llm.context import condense_logs
from ..sdk.capabilities import Capability
from ..sdk.models import Run, RunStatus, utcnow
from ..sdk.plugin import PlatformInstance
from ..security.redact import redact
from ..store.db import StateStore

log = logging.getLogger(__name__)

SOURCE = "observer"


@dataclass
class ObserveReport:
    runs_seen: int = 0
    events: list[tuple[str, str]] = field(default_factory=list)  # (kind, run_key)
    errors: list[str] = field(default_factory=list)


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


class Observer:
    def __init__(
        self,
        config: AgentConfig,
        instances: dict[str, PlatformInstance],
        store: StateStore,
        evidence: EvidenceStore,
        *,
        initial_lookback: timedelta = timedelta(minutes=60),
        overlap: timedelta = timedelta(minutes=5),
        max_error_chars: int = 12_000,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.config = config
        self.instances = instances
        self.store = store
        self.evidence = evidence
        self.events = EventLog(store)
        self.initial_lookback = initial_lookback
        self.overlap = overlap
        self.max_error_chars = max_error_chars
        self.clock = clock

    def tick(self) -> ObserveReport:
        report = ObserveReport()
        for instance_id, inst in self.instances.items():
            try:
                self._observe(instance_id, inst, report)
            except Exception as exc:  # one platform down must not stop the others
                log.exception("observer: %s failed", instance_id)
                report.errors.append(f"{instance_id}: {type(exc).__name__}: {exc}")
        self._watch(report)
        return report

    # platform runs ------------------------------------------------------------
    def _observe(self, instance_id: str, inst: PlatformInstance, report: ObserveReport) -> None:
        now = self.clock()
        mark = self.store.get_watermark(f"{SOURCE}:{instance_id}")
        since = (mark - self.overlap) if mark else now - self.initial_lookback
        for run in inst.run_source.list_runs(since):
            report.runs_seen += 1
            self.store.upsert_run(run, now=now)
            if run.status == RunStatus.FAILED and not self.events.exists("run_failed", run.key):
                self._failed(inst, run, report)
            elif run.status.is_active and not self.events.exists("run_slow", run.key):
                self._maybe_slow(run, now, report)
        self.store.set_watermark(f"{SOURCE}:{instance_id}", now)

    def _failure_text(self, inst: PlatformInstance, run: Run) -> tuple[str, dict]:
        if not inst.supports(Capability.LOGS):
            return run.raw.get("state_message") or "Run failed", {}
        ctx = inst.logs.failure_context(run)
        hints = {"error_class": ctx.error_class.value, "matched_rule": ctx.matched_rule, "failed_tasks": [t.task_key for t in ctx.tasks]}
        return ctx.full_text(), hints

    def _failed(self, inst: PlatformInstance, run: Run, report: ObserveReport, kind: str = "run_failed", extra: dict | None = None) -> None:
        text, hints = self._failure_text(inst, run)
        condensed = condense_logs(text, self.max_error_chars)
        ref = self.evidence.put_text(condensed, kind="failure_context", source=SOURCE, run_key=run.key)
        headline = redact(text.strip().splitlines()[0] if text.strip() else "Run failed").text[:500]
        payload = {**self._run_payload(run), **hints, "error_headline": headline, **(extra or {})}
        self.events.emit(kind, run.platform_id, run.key, payload, [ref.id], now=self.clock())
        report.events.append((kind, run.key))

    def _maybe_slow(self, run: Run, now: datetime, report: ObserveReport) -> None:
        policy = self.config.policy_for(run.platform_id, run.pipeline_name)
        threshold: float | None = float(policy.max_duration_seconds) if policy.max_duration_seconds else None
        if threshold is None:
            samples = self.store.successful_durations(run.platform_id, run.pipeline_id)
            if len(samples) >= policy.baseline_min_samples:
                threshold = p95(samples) * policy.baseline_multiplier
        elapsed = run.elapsed(now)
        if threshold is None or elapsed is None or elapsed.total_seconds() <= threshold:
            return
        payload = {**self._run_payload(run), "elapsed_seconds": int(elapsed.total_seconds()), "threshold_seconds": int(threshold)}
        self.events.emit("run_slow", run.platform_id, run.key, payload, now=now)
        report.events.append(("run_slow", run.key))

    @staticmethod
    def _run_payload(run: Run) -> dict:
        return {
            "pipeline_id": run.pipeline_id, "pipeline_name": run.pipeline_name, "run_id": run.run_id, "env": run.env.value,
            "status": run.status.value, "url": run.url, "start_time": run.start_time.isoformat() if run.start_time else None,
            "parameters": dict(run.parameters or {}),
        }

    # runs the executor started --------------------------------------------------
    def _watch(self, report: ObserveReport) -> None:
        for watch in self.events.unconsumed(("watch",)):
            inst = self.instances.get(watch.platform_id)
            if inst is None or inst.actions is None:
                continue
            try:
                run = inst.actions.get_run(watch.payload["run_id"])
            except Exception as exc:
                report.errors.append(f"watch {watch.run_key}: {exc}")
                continue
            since = watch.payload.get("since")
            # A repair reuses the failed run: until the platform picks it up, the old terminal state still shows.
            stale = since and run.end_time and run.end_time <= datetime.fromisoformat(since)
            if not run.status.is_terminal or stale:
                continue
            extra = {k: watch.payload.get(k) for k in ("request_id", "capability", "incident_id")}
            if run.status == RunStatus.FAILED:
                self._failed(inst, run, report, kind="run_finished", extra=extra)  # failure text (e.g. test output) as evidence
            else:
                self.events.emit("run_finished", run.platform_id, run.key, {**self._run_payload(run), **extra}, now=self.clock())
                report.events.append(("run_finished", run.key))
            self.events.consume([watch.id], now=self.clock())
