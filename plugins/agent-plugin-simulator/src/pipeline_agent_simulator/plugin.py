"""Simulated ETL platform.

Each configured pipeline has a behaviour (ok, slow, fail_code, fail_transient,
fail_permission, flaky). Runs progress against a clock, so the monitor sees
them start, run and finish exactly as it would on a real platform.

Optional ``state_file`` persists runs between processes, so repeated
``pipeline-agent once`` calls behave like a live platform.
"""

from __future__ import annotations

import hashlib
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
    # Job id, when the same pipeline exists in several envs (as on Databricks: one job per env). Defaults to the name.
    id: str | None = None
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

    @property
    def key(self) -> str:
        return self.id or self.name


class SimulatorConfig(BaseModel):
    pipelines: list[SimPipeline] = Field(default_factory=list)
    start_on_boot: bool = True
    state_file: str | None = None
    seed: int = 7
    repo: str | None = "example/demo-pipelines"
    # Data-plane actions (redesign)
    fix_on_sync: bool = True  # code.sync "deploys a fix": fail_code pipelines of that env succeed afterwards
    branch_heads: dict[str, str] = Field(default_factory=dict)  # env -> commit the Git folder ends up on
    test_outcomes: dict[str, Literal["pass", "fail"]] = Field(default_factory=dict)  # branch -> outcome (default pass)
    test_duration_seconds: int = 120
    profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)  # table -> profile to report


TESTS = "__tests__"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SimulatorBackend:
    """Implements RunSource, RunController, LogProvider, CostProvider, CodeLocator and ActionProvider."""

    def __init__(self, instance_id: str, config: SimulatorConfig, clock: Callable[[], datetime] = utcnow):
        self.instance_id = instance_id
        self.config = config
        self.clock = clock
        self._rng = random.Random(config.seed)
        self._by_name = {p.key: p for p in config.pipelines}  # keyed by job id
        self.runs: list[dict[str, Any]] = []
        self.attempts: dict[str, int] = {}
        self.fixed: set[str] = set()  # "<env>:<pipeline>" whose code was fixed by a sync
        self.heads: dict[str, str] = {}
        self._next_id = 1
        self._load()
        if config.start_on_boot and not self.runs:
            for p in config.pipelines:
                self.trigger(p.key, triggered_by="schedule")

    # persistence ---------------------------------------------------------
    def _load(self) -> None:
        if self.config.state_file and Path(self.config.state_file).exists():
            data = json.loads(Path(self.config.state_file).read_text(encoding="utf-8"))
            self.runs, self.attempts, self._next_id = data["runs"], data["attempts"], data["next_id"]
            self.fixed, self.heads = set(data.get("fixed", [])), data.get("heads", {})

    def _save(self) -> None:
        if self.config.state_file:
            data = {"runs": self.runs, "attempts": self.attempts, "next_id": self._next_id, "fixed": sorted(self.fixed), "heads": self.heads}
            Path(self.config.state_file).write_text(json.dumps(data, indent=2), encoding="utf-8")

    # simulation ----------------------------------------------------------
    def _outcome(self, p: SimPipeline) -> tuple[int, str, str | None]:
        """(duration, outcome, error) of the next attempt of ``p``."""
        attempt = self.attempts.get(p.key, 0)
        self.attempts[p.key] = attempt + 1
        duration, outcome = p.duration_seconds, "succeeded"
        if p.behaviour == "slow":
            duration = p.slow_duration_seconds
        elif p.behaviour == "fail_code" and f"{p.env.value}:{p.name}" not in self.fixed:
            outcome = "failed"
        elif p.behaviour == "fail_permission":
            outcome = "failed"
        elif p.behaviour == "fail_transient" and attempt < p.fail_attempts:
            outcome = "failed"
        elif p.behaviour == "flaky" and self._rng.random() < p.flaky_rate:
            outcome = "failed"
        error = (p.error_message or DEFAULT_ERRORS[p.behaviour]) if outcome == "failed" else None
        return duration, outcome, error

    def trigger(self, name: str, triggered_by: str = "manual", params: dict[str, str] | None = None) -> dict[str, Any]:
        start = self.clock()
        duration, outcome, error = self._outcome(self._by_name[name])
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
        p = self._by_name.get(raw["pipeline"]) or SimPipeline(name=raw["pipeline"], env=Env(raw.get("env", "dev")))
        start, planned_end, cancelled = _parse(raw["start"]), _parse(raw["planned_end"]), _parse(raw["cancelled_at"])
        if cancelled:
            status, end = RunStatus.CANCELLED, cancelled
        elif now < planned_end:
            status, end = RunStatus.RUNNING, None
        else:
            status, end = RunStatus(raw["outcome"]), planned_end
        return Run(
            platform_id=self.instance_id,
            pipeline_id=p.key,
            pipeline_name=p.name,
            run_id=raw["run_id"],
            status=status,
            env=p.env,
            start_time=start,
            end_time=end,
            url=f"sim://{self.instance_id}/{p.key}/runs/{raw['run_id']}",
            triggered_by=raw["triggered_by"],
            parameters=raw["params"],
            raw=raw,
        )

    # RunSource -----------------------------------------------------------
    def list_pipelines(self) -> list[Pipeline]:
        return [
            Pipeline(platform_id=self.instance_id, pipeline_id=p.key, name=p.name, env=p.env, url=f"sim://{self.instance_id}/{p.key}")
            for p in self.config.pipelines
        ]

    def list_runs(self, since: datetime, active_only: bool = False) -> list[Run]:
        now = self.clock()
        runs = [self._to_run(r, now) for r in self.runs if r["pipeline"] != TESTS]  # one-time test runs aren't pipelines
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
            p = self._by_name[run.pipeline_id]
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

    # ActionProvider (data plane) -----------------------------------------
    def _raw(self, run_id: str) -> dict[str, Any]:
        for raw in self.runs:
            if raw["run_id"] == str(run_id):
                return raw
        raise KeyError(f"run {run_id} not found")

    def get_run(self, run_id: str) -> Run:
        return self._to_run(self._raw(run_id), self.clock())

    def repair(self, run: Run) -> dict[str, Any]:
        raw = self._raw(run.run_id)
        duration, outcome, error = self._outcome(self._by_name[raw["pipeline"]])
        raw["repairs"] = raw.get("repairs", 0) + 1
        raw.update(planned_end=_iso(self.clock() + timedelta(seconds=duration)), outcome=outcome, error=error, cancelled_at=None)
        self._save()
        return {"run_id": run.run_id, "repair_id": f"{run.run_id}-r{raw['repairs']}", "pipeline_id": run.pipeline_id}

    def sync_code(self, env: str) -> dict[str, Any]:
        before = self.heads.get(env)
        self.heads[env] = self.config.branch_heads.get(env, f"sim-{env}-head")
        if self.config.fix_on_sync:
            self.fixed |= {f"{env}:{p.name}" for p in self.config.pipelines if p.env.value == env and p.behaviour == "fail_code"}
        self._save()
        branch = "main" if env == "prod" else env
        return {"path": f"sim://{self.instance_id}/{env}", "branch": branch, "head_before": before, "head": self.heads[env]}

    def run_tests(self, branch: str, notebooks: list[str], params: dict[str, str]) -> dict[str, Any]:
        start = self.clock()
        failed = self.config.test_outcomes.get(branch, "pass") == "fail"
        raw = {
            "run_id": str(self._next_id), "pipeline": TESTS, "env": "dev", "start": _iso(start),
            "planned_end": _iso(start + timedelta(seconds=self.config.test_duration_seconds)),
            "outcome": "failed" if failed else "succeeded",
            "error": f"AssertionError: {notebooks[0]} failed on {branch}" if failed else None,
            "cancelled_at": None, "triggered_by": "agent", "params": {"branch": branch, **params},
        }
        self._next_id += 1
        self.runs.append(raw)
        self._save()
        return {"run_id": raw["run_id"], "branch": branch, "notebooks": notebooks}

    def profile(self, table: str, keys: list[str], not_null: list[str]) -> dict[str, Any]:
        default = {"rows": 100, "duplicate_keys": 0, "nulls": {c: 0 for c in not_null}}
        return {"table": table, **self.config.profiles.get(table, default)}

    def definition_hash(self, pipeline_id: str) -> str:
        return hashlib.sha256(self._by_name[pipeline_id].model_dump_json().encode()).hexdigest()

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
        capabilities={
            Capability.MONITOR, Capability.CANCEL, Capability.RERUN, Capability.LOGS, Capability.COST, Capability.CODE, Capability.ACTIONS,
        },
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
            actions=backend,
            error_rules=rules,
            healthcheck=lambda: f"ok ({len(backend.config.pipelines)} simulated pipelines)",
        )
        instance.backend = backend  # handy for tests and demo scripts
        return instance
