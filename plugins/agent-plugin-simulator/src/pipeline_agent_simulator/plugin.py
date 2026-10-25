"""Simulated ETL platform.

Each configured pipeline has a behaviour (ok, slow, fail_code, fail_transient,
fail_permission, flaky). Runs progress against a clock, so the monitor sees
them start, run and finish exactly as it would on a real platform.

Optional ``state_file`` persists runs between processes, so repeated
``pipeline-agent once`` calls behave like a live platform.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field

from pipeline_agent import PLUGIN_API_VERSION
from pipeline_agent.sdk import (
    Capability,
    CodeRef,
    CostRecord,
    Env,
    ErrorClassifier,
    FailureContext,
    Pipeline,
    PlatformInstance,
    PlatformPlugin,
    PluginManifest,
    Run,
    RunRef,
    RunStatus,
    TaskFailure,
    parse_rules,
    utcnow,
)

Behaviour = Literal["ok", "slow", "fail_code", "fail_transient", "fail_permission", "flaky"]

DEFAULT_ERRORS: dict[str, str] = {
    "fail_code": "[CAST_INVALID_INPUT] The value 'abc' of the type \"STRING\" cannot be cast to \"INT\" "
    "because it is malformed. Column: qty. SQLSTATE: 22018",
    "fail_transient": "ConnectionError: Connection reset by peer while calling partner API (HTTP 503)",
    "fail_permission": "[PERMISSION_DENIED] User does not have SELECT on Table 'prod.bronze.orders'. SQLSTATE: 42501",
    "flaky": "ConnectionError: Connection reset by peer while calling partner API (HTTP 503)",
}


class SimPipeline(BaseModel):
    name: str
    env: Env = Env.PROD
    behaviour: Behaviour = "ok"
    duration_seconds: int = Field(60, ge=1)
    # "slow" runs take this long instead.
    slow_duration_seconds: int = Field(3600, ge=1)
    # "fail_transient": the first N runs fail, later runs succeed.
    fail_attempts: int = Field(1, ge=0)
    flaky_rate: float = Field(0.5, ge=0, le=1)
    error_message: str | None = None
    dbu_per_hour: float = 2.0
    code_path: str | None = None


class SimulatorConfig(BaseModel):
    pipelines: list[SimPipeline] = Field(default_factory=list)
    start_on_boot: bool = True
    state_file: str | None = None
    seed: int = 7
    repo: str | None = "example/demo-pipelines"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SimulatorBackend:
    """Implements RunSource, RunController, LogProvider, CostProvider and CodeLocator."""

    def __init__(self, instance_id: str, config: SimulatorConfig, clock: Callable[[], datetime] = utcnow):
        self.instance_id = instance_id
        self.config = config
        self.clock = clock
        self._rng = random.Random(config.seed)
        self._by_name = {p.name: p for p in config.pipelines}
        self.runs: list[dict[str, Any]] = []
        self.attempts: dict[str, int] = {}
        self._next_id = 1
        self._load()
        if config.start_on_boot and not self.runs:
            for p in config.pipelines:
                self.trigger(p.name, triggered_by="schedule")

    # persistence ---------------------------------------------------------
    def _load(self) -> None:
        if self.config.state_file and Path(self.config.state_file).exists():
            data = json.loads(Path(self.config.state_file).read_text(encoding="utf-8"))
            self.runs, self.attempts, self._next_id = data["runs"], data["attempts"], data["next_id"]

    def _save(self) -> None:
        if self.config.state_file:
            data = {"runs": self.runs, "attempts": self.attempts, "next_id": self._next_id}
            Path(self.config.state_file).write_text(json.dumps(data, indent=2), encoding="utf-8")

    # simulation ----------------------------------------------------------
    def trigger(self, name: str, triggered_by: str = "manual", params: dict[str, str] | None = None) -> dict[str, Any]:
        p = self._by_name[name]
        attempt = self.attempts.get(name, 0)
        self.attempts[name] = attempt + 1
        start = self.clock()
        duration, outcome, error = p.duration_seconds, "succeeded", None
        if p.behaviour == "slow":
            duration = p.slow_duration_seconds
        elif p.behaviour in ("fail_code", "fail_permission"):
            outcome = "failed"
        elif p.behaviour == "fail_transient" and attempt < p.fail_attempts:
            outcome = "failed"
        elif p.behaviour == "flaky" and self._rng.random() < p.flaky_rate:
            outcome = "failed"
        if outcome == "failed":
            error = p.error_message or DEFAULT_ERRORS[p.behaviour]
        run = {
            "run_id": str(self._next_id),
            "pipeline": name,
            "start": _iso(start),
            "planned_end": _iso(start + timedelta(seconds=duration)),
            "outcome": outcome,
            "error": error,
            "cancelled_at": None,
            "triggered_by": triggered_by,
            "params": params or {},
        }
        self._next_id += 1
        self.runs.append(run)
        self._save()
        return run

    def _to_run(self, raw: dict[str, Any], now: datetime) -> Run:
        p = self._by_name[raw["pipeline"]]
        start, planned_end, cancelled = _parse(raw["start"]), _parse(raw["planned_end"]), _parse(raw["cancelled_at"])
        if cancelled:
            status, end = RunStatus.CANCELLED, cancelled
        elif now < planned_end:
            status, end = RunStatus.RUNNING, None
        else:
            status, end = RunStatus(raw["outcome"]), planned_end
        return Run(
            platform_id=self.instance_id,
            pipeline_id=p.name,
            pipeline_name=p.name,
            run_id=raw["run_id"],
            status=status,
            env=p.env,
            start_time=start,
            end_time=end,
            url=f"sim://{self.instance_id}/{p.name}/runs/{raw['run_id']}",
            triggered_by=raw["triggered_by"],
            parameters=raw["params"],
            raw=raw,
        )

    # RunSource -----------------------------------------------------------
    def list_pipelines(self) -> list[Pipeline]:
        return [
            Pipeline(platform_id=self.instance_id, pipeline_id=p.name, name=p.name, env=p.env, url=f"sim://{self.instance_id}/{p.name}")
            for p in self.config.pipelines
        ]

    def list_runs(self, since: datetime, active_only: bool = False) -> list[Run]:
        now = self.clock()
        runs = [self._to_run(r, now) for r in self.runs]
        if active_only:
            return [r for r in runs if r.status.is_active]
        return [r for r in runs if r.status.is_active or (r.end_time and r.end_time >= since)]

    # RunController -------------------------------------------------------
    def cancel(self, run: Run) -> None:
        for raw in self.runs:
            if raw["run_id"] == run.run_id and raw["cancelled_at"] is None:
                raw["cancelled_at"] = _iso(self.clock())
        self._save()

    def rerun(self, pipeline: Pipeline, params: dict[str, str] | None = None) -> RunRef:
        raw = self.trigger(pipeline.pipeline_id, triggered_by="agent", params=params)
        return RunRef(platform_id=self.instance_id, pipeline_id=pipeline.pipeline_id, run_id=raw["run_id"])

    # LogProvider ---------------------------------------------------------
    def failure_context(self, run: Run) -> FailureContext:
        error = run.raw.get("error") or "Run failed"
        return FailureContext(
            run=run,
            error_message=error,
            tasks=[TaskFailure(task_key="main", error_message=error, error_trace=f"Traceback (simulated)\n{error}")],
        )

    # CostProvider --------------------------------------------------------
    def costs(self, since: datetime) -> list[CostRecord]:
        records = []
        for run in self.list_runs(since):
            if run.end_time is None or run.start_time is None:
                continue
            p = self._by_name[run.pipeline_name]
            dbu = (run.end_time - run.start_time).total_seconds() / 3600 * p.dbu_per_hour
            records.append(
                CostRecord(
                    platform_id=self.instance_id,
                    usage_date=run.end_time.date(),
                    quantity=round(dbu, 4),
                    unit="DBU",
                    pipeline_id=run.pipeline_id,
                    run_id=run.run_id,
                    env=run.env,
                    is_estimate=True,
                )
            )
        return records

    # CodeLocator ---------------------------------------------------------
    def code_ref(self, pipeline: Pipeline) -> CodeRef | None:
        p = self._by_name.get(pipeline.pipeline_id)
        if not self.config.repo or p is None or not p.code_path:
            return None
        return CodeRef(repo=self.config.repo, path=p.code_path, ref="main", deploy_target=pipeline.env.value)


def _rules():
    text = resources.files("pipeline_agent_simulator").joinpath("knowledge/errors.yaml").read_text(encoding="utf-8")
    return parse_rules(yaml.safe_load(text))


class SimulatorPlugin(PlatformPlugin):
    config_model: ClassVar[type[BaseModel]] = SimulatorConfig
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="simulator",
        version="0.1.0",
        plugin_api_version=PLUGIN_API_VERSION,
        display_name="Simulator",
        description="Simulated pipelines for demos and tests",
        capabilities={Capability.MONITOR, Capability.CANCEL, Capability.RERUN, Capability.LOGS, Capability.COST, Capability.CODE},
        config_schema=SimulatorConfig.model_json_schema(),
        secrets=[],
    )

    def build(
        self,
        instance_id: str,
        config: dict[str, Any],
        secrets: dict[str, str],
        clock: Callable[[], datetime] = utcnow,
    ) -> PlatformInstance:
        backend = SimulatorBackend(instance_id, SimulatorConfig.model_validate(config), clock)
        rules = _rules()
        classifier = ErrorClassifier.with_defaults(rules)

        class _Logs:
            def failure_context(self, run: Run) -> FailureContext:
                ctx = backend.failure_context(run)
                ctx.error_class, ctx.matched_rule = classifier.classify(ctx.full_text())
                return ctx

        instance = PlatformInstance(
            instance_id,
            self.manifest,
            run_source=backend,
            controller=backend,
            logs=_Logs(),
            costs=backend,
            code=backend,
            error_rules=rules,
            healthcheck=lambda: f"ok ({len(backend.config.pipelines)} simulated pipelines)",
        )
        instance.backend = backend  # handy for tests and demo scripts
        return instance
