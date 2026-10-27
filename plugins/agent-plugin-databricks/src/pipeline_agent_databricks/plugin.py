"""Databricks Jobs plugin (Jobs API 2.2 through databricks-sdk).

Environment mapping: all environments can live in one workspace (Free Edition
has only one). Job names carry a prefix per environment, set by the Asset
Bundle target (``presets.name_prefix``), e.g. ``[dev] orders_pipeline``. Jobs
with no known prefix are treated as prod.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from importlib import resources
from typing import Any, ClassVar

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
)


class DatabricksConfig(BaseModel):
    host: str | None = Field(
        None, description="Workspace URL, e.g. https://dbc-xxxx.cloud.databricks.com (or secret 'host')"
    )
    env_prefixes: dict[str, Env] = Field(
        default_factory=lambda: {"[dev] ": Env.DEV, "[qa] ": Env.QA},
        description="Job-name prefix -> environment. Jobs without a known prefix are prod.",
    )
    include: list[str] = Field(default_factory=lambda: ["*"], description="Globs on the job name (without prefix)")
    # Runs that started up to this long before the poll window are still checked,
    # so long-running runs that finish inside the window are not missed.
    max_run_lookback_hours: int = 24
    max_log_chars: int = 20_000
    # Cost: system.billing.usage needs a SQL warehouse. Otherwise cost is estimated from run duration.
    use_billing_table: bool = False
    warehouse_id: str | None = None
    estimated_dbu_per_hour: float = 1.0
    dbu_rate_usd: float = 0.0
    # Code location, used by the fix flow.
    repo: str | None = None
    code_root: str = "notebooks"
    code_paths: dict[str, str] = Field(default_factory=dict, description="Job name -> path in the repo")
    # Data-plane actions (redesign): where each env's code is checked out, and the repo tests run from.
    git_url: str | None = Field(None, description="https URL of the pipelines repo (tests run from its branches)")
    git_folders: dict[str, str] = Field(
        default_factory=dict, description="env -> Databricks Git folder path, e.g. dev: /Workspace/Shared/orders-pipelines-dev"
    )


_RESULT_MAP = {
    "SUCCESS": RunStatus.SUCCEEDED,
    "SUCCESS_WITH_FAILURES": RunStatus.FAILED,
    "FAILED": RunStatus.FAILED,
    "TIMEDOUT": RunStatus.FAILED,
    "UPSTREAM_FAILED": RunStatus.FAILED,
    "MAXIMUM_CONCURRENT_RUNS_REACHED": RunStatus.FAILED,
    "CANCELED": RunStatus.CANCELLED,
    "UPSTREAM_CANCELED": RunStatus.CANCELLED,
    "DISABLED": RunStatus.SKIPPED,
    "EXCLUDED": RunStatus.SKIPPED,
}
_FAILED_TASK_RESULTS = {"FAILED", "TIMEDOUT", "MAXIMUM_CONCURRENT_RUNS_REACHED"}


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def map_status(life_cycle_state: str | None, result_state: str | None) -> RunStatus:
    if life_cycle_state in {"PENDING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"}:
        return RunStatus.QUEUED
    if life_cycle_state in {"RUNNING", "TERMINATING"}:
        return RunStatus.RUNNING
    if life_cycle_state == "SKIPPED":
        return RunStatus.SKIPPED
    if life_cycle_state == "INTERNAL_ERROR":
        return RunStatus.FAILED
    if life_cycle_state == "TERMINATED":
        return _RESULT_MAP.get(result_state or "", RunStatus.UNKNOWN)
    return RunStatus.UNKNOWN


def _ms_to_dt(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class DatabricksBackend:
    """RunSource, RunController, LogProvider, CostProvider, CodeLocator and ActionProvider for one workspace."""

    def __init__(self, instance_id: str, config: DatabricksConfig, client: Any):
        self.instance_id = instance_id
        self.config = config
        self.client = client
        self.classifier = ErrorClassifier.with_defaults(_rules())

    # naming --------------------------------------------------------------
    def split_name(self, full_name: str) -> tuple[str, Env]:
        for prefix, env in self.config.env_prefixes.items():
            if full_name.startswith(prefix):
                return full_name[len(prefix):], Env(env)
        return full_name, Env.PROD

    def _included(self, base_name: str) -> bool:
        return any(fnmatch.fnmatchcase(base_name, pattern) for pattern in self.config.include)

    def _job_url(self, job_id: Any) -> str:
        return f"{self.config.host.rstrip('/')}/jobs/{job_id}"

    # RunSource -----------------------------------------------------------
    def list_pipelines(self) -> list[Pipeline]:
        pipelines = []
        for job in self.client.jobs.list():
            full_name = (job.settings.name if job.settings else None) or str(job.job_id)
            base, env = self.split_name(full_name)
            if not self._included(base):
                continue
            pipelines.append(
                Pipeline(
                    platform_id=self.instance_id,
                    pipeline_id=str(job.job_id),
                    name=base,
                    env=env,
                    url=self._job_url(job.job_id),
                    tags=dict(job.settings.tags or {}) if job.settings and job.settings.tags else {},
                    raw={"full_name": full_name},
                )
            )
        return pipelines

    def _to_run(self, r: Any) -> Run:
        lifecycle = _enum_value(r.state.life_cycle_state) if r.state else None
        result = _enum_value(r.state.result_state) if r.state else None
        base, env = self.split_name(r.run_name or str(r.job_id))
        params = {p.name: (p.value if p.value is not None else p.default) for p in (r.job_parameters or []) if p.name}
        return Run(
            platform_id=self.instance_id,
            pipeline_id=str(r.job_id),
            pipeline_name=base,
            run_id=str(r.run_id),
            status=map_status(lifecycle, result),
            env=env,
            start_time=_ms_to_dt(r.start_time),
            end_time=_ms_to_dt(r.end_time),
            url=r.run_page_url,
            triggered_by=_enum_value(r.trigger),
            parameters={k: str(v) for k, v in params.items() if v is not None},
            raw={
                "life_cycle_state": lifecycle,
                "result_state": result,
                "state_message": r.state.state_message if r.state else None,
            },
        )

    def list_runs(self, since: datetime, active_only: bool = False) -> list[Run]:
        runs: dict[str, Run] = {}
        for r in self.client.jobs.list_runs(active_only=True):
            run = self._to_run(r)
            runs[run.run_id] = run
        if not active_only:
            start_from = since - timedelta(hours=self.config.max_run_lookback_hours)
            for r in self.client.jobs.list_runs(completed_only=True, start_time_from=int(start_from.timestamp() * 1000)):
                run = self._to_run(r)
                if run.end_time and run.end_time >= since:
                    runs[run.run_id] = run
        return [r for r in runs.values() if self._included(r.pipeline_name)]

    # RunController -------------------------------------------------------
    def cancel(self, run: Run) -> None:
        self.client.jobs.cancel_run(run_id=int(run.run_id))  # returns a waiter; we don't block on it

    def rerun(self, pipeline: Pipeline, params: dict[str, str] | None = None) -> RunRef:
        waiter = self.client.jobs.run_now(job_id=int(pipeline.pipeline_id), job_parameters=params or None)
        run_id = getattr(getattr(waiter, "response", None), "run_id", None) or waiter.run_id
        return RunRef(platform_id=self.instance_id, pipeline_id=pipeline.pipeline_id, run_id=str(run_id))

    # LogProvider ---------------------------------------------------------
    def _clip(self, text: str | None) -> str | None:
        if text and len(text) > self.config.max_log_chars:
            return text[-self.config.max_log_chars:]  # keep the end: that's where the error is
        return text

    def failure_context(self, run: Run) -> FailureContext:
        detail = self.client.jobs.get_run(run_id=int(run.run_id))
        tasks: list[TaskFailure] = []
        for task in detail.tasks or []:
            result = _enum_value(task.state.result_state) if task.state else None
            if result not in _FAILED_TASK_RESULTS:
                continue
            error, trace, logs = (task.state.state_message if task.state else "") or "", None, None
            try:
                out = self.client.jobs.get_run_output(run_id=int(task.run_id))
                error, trace, logs = out.error or error, out.error_trace, out.logs
            except Exception as exc:  # output can be unavailable (e.g. expired); keep what we have
                trace = f"(run output unavailable: {exc})"
            tasks.append(TaskFailure(task_key=task.task_key, error_message=error, error_trace=self._clip(trace), logs=self._clip(logs)))
        # The run-level state message is generic ("Task x failed ... see run output");
        # the failed task's own error is the useful headline.
        state_message = (detail.state.state_message if detail.state else None) or ""
        task_error = next((t.error_message for t in tasks if t.error_message), "")
        message = task_error or state_message or "Run failed"
        if task_error and state_message:
            message = f"{task_error}\n({state_message})"
        ctx = FailureContext(run=run, error_message=message, tasks=tasks)
        ctx.error_class, ctx.matched_rule = self.classifier.classify(ctx.full_text())
        return ctx

    # CostProvider --------------------------------------------------------
    def costs(self, since: datetime) -> list[CostRecord]:
        if self.config.use_billing_table and self.config.warehouse_id:
            return self._billing_costs(since)
        return self._estimated_costs(since)

    def _billing_costs(self, since: datetime) -> list[CostRecord]:
        from databricks.sdk.service.sql import StatementParameterListItem

        envs = {p.pipeline_id: p.env for p in self.list_pipelines()}
        resp = self.client.statement_execution.execute_statement(
            statement=(
                "SELECT usage_metadata.job_id, usage_metadata.job_run_id, usage_date, SUM(usage_quantity) "
                "FROM system.billing.usage "
                "WHERE usage_date >= :since AND usage_metadata.job_id IS NOT NULL "
                "GROUP BY ALL"
            ),
            warehouse_id=self.config.warehouse_id,
            parameters=[StatementParameterListItem(name="since", value=since.date().isoformat(), type="DATE")],
            wait_timeout="30s",
        )
        state = _enum_value(resp.status.state) if resp.status else None
        if state != "SUCCEEDED":
            raise RuntimeError(f"billing query did not succeed: {state}")
        records = []
        for job_id, run_id, usage_date, dbus in (resp.result.data_array if resp.result else None) or []:
            dbus = float(dbus or 0)
            records.append(
                CostRecord(
                    platform_id=self.instance_id,
                    usage_date=date.fromisoformat(str(usage_date)),
                    quantity=dbus,
                    unit="DBU",
                    pipeline_id=str(job_id),
                    run_id=str(run_id) if run_id else None,
                    env=envs.get(str(job_id)),
                    estimated_cost_usd=round(dbus * self.config.dbu_rate_usd, 4),
                    is_estimate=False,
                )
            )
        return records

    def _estimated_costs(self, since: datetime) -> list[CostRecord]:
        records = []
        for run in self.list_runs(since):
            if not (run.start_time and run.end_time):
                continue
            dbus = (run.end_time - run.start_time).total_seconds() / 3600 * self.config.estimated_dbu_per_hour
            records.append(
                CostRecord(
                    platform_id=self.instance_id,
                    usage_date=run.end_time.date(),
                    quantity=round(dbus, 4),
                    unit="DBU",
                    pipeline_id=run.pipeline_id,
                    run_id=run.run_id,
                    env=run.env,
                    estimated_cost_usd=round(dbus * self.config.dbu_rate_usd, 4),
                    is_estimate=True,
                )
            )
        return records

    # CodeLocator ---------------------------------------------------------
    def code_ref(self, pipeline: Pipeline) -> CodeRef | None:
        if not self.config.repo:
            return None
        path = self.config.code_paths.get(pipeline.name, self.config.code_root)
        branch = {Env.DEV: "dev", Env.QA: "qa", Env.PROD: "main"}[pipeline.env]
        return CodeRef(repo=self.config.repo, path=path, ref=branch, deploy_target=pipeline.env.value)

    # ActionProvider (data plane) -----------------------------------------
    def get_run(self, run_id: str) -> Run:
        return self._to_run(self.client.jobs.get_run(run_id=int(run_id)))

    def repair(self, run: Run) -> dict[str, Any]:
        waiter = self.client.jobs.repair_run(run_id=int(run.run_id), rerun_all_failed_tasks=True)
        repair_id = getattr(getattr(waiter, "response", None), "repair_id", None) or getattr(waiter, "repair_id", None)
        return {"run_id": run.run_id, "repair_id": str(repair_id) if repair_id else None, "pipeline_id": run.pipeline_id}

    def sync_code(self, env: str) -> dict[str, Any]:
        path = self.config.git_folders.get(env)
        if not path:
            raise ValueError(f"no Git folder configured for env {env!r} (config.git_folders)")
        # Free Edition reports Git folders as DIRECTORY and repos.list skips them; repos.get(object_id) works (R0).
        repo_id = self.client.workspace.get_status(path).object_id
        before = self.client.repos.get(repo_id)
        self.client.repos.update(repo_id, branch=before.branch)  # pull the branch head
        after = self.client.repos.get(repo_id)
        return {"path": path, "branch": after.branch, "head_before": before.head_commit_id, "head": after.head_commit_id}

    def run_tests(self, branch: str, notebooks: list[str], params: dict[str, str]) -> dict[str, Any]:
        from databricks.sdk.service.jobs import GitProvider, GitSource, NotebookTask, RunIf, Source, SubmitTask, TaskDependency

        if not self.config.git_url:
            raise ValueError("config.git_url is not set")
        tasks, previous = [], None
        for nb in notebooks:
            key = re.sub(r"[^A-Za-z0-9_-]", "_", nb.rsplit("/", 1)[-1])[:100]
            # Sequential (Free Edition: max 5 concurrent tasks) but every test runs even if an earlier one failed.
            tasks.append(
                SubmitTask(
                    task_key=key,
                    notebook_task=NotebookTask(notebook_path=nb.removesuffix(".py"), source=Source.GIT, base_parameters=params),
                    depends_on=[TaskDependency(task_key=previous)] if previous else None,
                    run_if=RunIf.ALL_DONE if previous else None,
                )
            )
            previous = key
        waiter = self.client.jobs.submit(
            run_name=f"pipeline-agent tests @ {branch}",
            git_source=GitSource(git_url=self.config.git_url, git_provider=GitProvider.GIT_HUB, git_branch=branch),
            tasks=tasks,
        )
        run_id = getattr(getattr(waiter, "response", None), "run_id", None) or waiter.run_id
        return {"run_id": str(run_id), "branch": branch, "notebooks": notebooks}

    _IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")

    def profile(self, table: str, keys: list[str], not_null: list[str]) -> dict[str, Any]:
        for name in [table, *keys, *not_null]:
            if not self._IDENT.match(name):
                raise ValueError(f"invalid identifier {name!r}")
        if not self.config.warehouse_id:
            raise ValueError("data.profile needs a SQL warehouse (secrets.warehouse_id)")
        dup = f"COUNT(*) - COUNT(DISTINCT {', '.join(keys)})" if keys else "0"
        nulls = "".join(f", COUNT_IF({c} IS NULL)" for c in not_null)
        resp = self.client.statement_execution.execute_statement(
            statement=f"SELECT COUNT(*), {dup}{nulls} FROM {table}", warehouse_id=self.config.warehouse_id, wait_timeout="30s"
        )
        state = _enum_value(resp.status.state) if resp.status else None
        if state != "SUCCEEDED":
            raise RuntimeError(f"profile query did not succeed: {state}")
        row = resp.result.data_array[0]
        return {
            "table": table, "rows": int(row[0]), "duplicate_keys": int(row[1]),
            "nulls": {c: int(v) for c, v in zip(not_null, row[2:])},
        }

    def definition_hash(self, pipeline_id: str) -> str:
        settings = self.client.jobs.get(job_id=int(pipeline_id)).settings
        data = settings.as_dict() if hasattr(settings, "as_dict") else vars(settings)
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    # health --------------------------------------------------------------
    def healthcheck(self) -> str:
        self.client.current_user.me()
        return f"ok (authenticated; {len(self.list_pipelines())} jobs visible)"


def _rules():
    text = resources.files("pipeline_agent_databricks").joinpath("knowledge/errors.yaml").read_text(encoding="utf-8")
    return parse_rules(yaml.safe_load(text))


class DatabricksPlugin(PlatformPlugin):
    config_model: ClassVar[type[BaseModel]] = DatabricksConfig
    manifest: ClassVar[PluginManifest] = PluginManifest(
        name="databricks",
        version="0.1.0",
        plugin_api_version=PLUGIN_API_VERSION,
        display_name="Databricks",
        description="Databricks Jobs (Jobs API 2.2): monitor, cancel, rerun, repair, logs, cost, code location, Git-folder sync, tests",
        capabilities={
            Capability.MONITOR, Capability.CANCEL, Capability.RERUN, Capability.LOGS, Capability.COST, Capability.CODE, Capability.ACTIONS,
        },
        config_schema=DatabricksConfig.model_json_schema(),
        secrets=["token", "host", "warehouse_id"],
    )

    def build(
        self, instance_id: str, config: dict[str, Any], secrets: dict[str, str], client: Any = None
    ) -> PlatformInstance:
        cfg = DatabricksConfig.model_validate(config)
        # Workspace-identifying values can come from secret refs, keeping them out of committed config.
        if secrets.get("host"):
            cfg.host = secrets["host"]
        if secrets.get("warehouse_id"):
            cfg.warehouse_id = secrets["warehouse_id"]
        if not cfg.host:
            raise ValueError(f"{instance_id}: set config.host or secrets.host")
        if client is None:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient(host=cfg.host, token=secrets["token"])
        backend = DatabricksBackend(instance_id, cfg, client)
        instance = PlatformInstance(
            instance_id,
            self.manifest,
            run_source=backend,
            controller=backend,
            logs=backend,
            costs=backend,
            code=backend if cfg.repo else None,
            actions=backend,
            error_rules=_rules(),
            healthcheck=backend.healthcheck,
        )
        instance.backend = backend
        return instance
