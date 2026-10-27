"""Databricks plugin against a fake WorkspaceClient (shapes match databricks-sdk 0.15x)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from pipeline_agent.sdk import Env, ErrorClass, RunStatus
from pipeline_agent.testing import PluginContractSuite
from pipeline_agent_databricks import DatabricksPlugin, map_status

NOW = datetime.now(timezone.utc)
FAKE_TOKEN = "dapi-test-token-0000000000000000"


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def state(life, result=None, message=""):
    return NS(life_cycle_state=NS(value=life), result_state=NS(value=result) if result else None, state_message=message)


def run(run_id, job_id, name, life, result=None, start=None, end=None, params=None, tasks=None, message=""):
    return NS(
        run_id=run_id,
        job_id=job_id,
        run_name=name,
        state=state(life, result, message),
        start_time=ms(start or NOW - timedelta(minutes=10)),
        end_time=ms(end) if end else 0,
        run_page_url=f"https://dbc.example/jobs/{job_id}/runs/{run_id}",
        trigger=NS(value="PERIODIC"),
        job_parameters=[NS(name=k, value=v, default=None) for k, v in (params or {}).items()],
        tasks=tasks or [],
    )


class FakeJobs:
    def __init__(self):
        self.jobs = [
            NS(job_id=1, settings=NS(name="orders_pipeline", tags={"team": "data"})),
            NS(job_id=2, settings=NS(name="[dev] orders_pipeline", tags=None)),
            NS(job_id=3, settings=NS(name="[qa] sales_report", tags=None)),
            NS(job_id=4, settings=NS(name="scratch_job", tags=None)),
        ]
        failed_task = NS(task_key="transform_silver", run_id=1001, state=state("TERMINATED", "FAILED", "Task failed"))
        ok_task = NS(task_key="ingest_bronze", run_id=1000, state=state("TERMINATED", "SUCCESS"))
        self.active = [run(10, 3, "[qa] sales_report", "RUNNING", params={"sleep_seconds": "900"})]
        self.completed = [
            run(11, 1, "orders_pipeline", "TERMINATED", "FAILED", end=NOW - timedelta(minutes=1), tasks=[ok_task, failed_task]),
            run(12, 1, "orders_pipeline", "TERMINATED", "SUCCESS", end=NOW - timedelta(days=3)),  # outside window
            run(13, 4, "scratch_job", "TERMINATED", "SUCCESS", end=NOW - timedelta(minutes=1)),  # not included
        ]
        self.cancelled, self.started = [], []

    def list(self, **kw):
        return iter(self.jobs)

    def list_runs(self, active_only=None, completed_only=None, start_time_from=None, **kw):
        return iter(self.active if active_only else self.completed)

    def get_run(self, run_id):
        return next(r for r in self.completed + self.active if r.run_id == run_id)

    def get_run_output(self, run_id):
        return NS(
            error="[CAST_INVALID_INPUT] The value 'abc' of the type \"STRING\" cannot be cast to \"INT\"",
            error_trace="Traceback ...\npyspark.errors.exceptions.NumberFormatException: CAST_INVALID_INPUT",
            logs="reading /Volumes/prod/bronze/landing",
        )

    def cancel_run(self, run_id):
        self.cancelled.append(run_id)

    def run_now(self, job_id, job_parameters=None):
        self.started.append((job_id, job_parameters))
        return NS(response=NS(run_id=999))


def fake_client():
    return NS(jobs=FakeJobs(), current_user=NS(me=lambda: NS(user_name="bot")))


CONFIG = {
    "host": "https://dbc.example",
    "include": ["orders_*", "sales_*"],
    "repo": "org/demo-pipelines",
    "code_paths": {"orders_pipeline": "sample_pipelines/src"},
}


class TestDatabricksContract(PluginContractSuite):
    secret_values = (FAKE_TOKEN,)

    @pytest.fixture
    def plugin(self):
        return DatabricksPlugin()

    @pytest.fixture
    def instance(self, plugin):
        return plugin.build("dbx", CONFIG, {"token": FAKE_TOKEN}, client=fake_client())

    @pytest.fixture
    def failed_run(self, instance):
        return next(r for r in instance.run_source.list_runs(NOW - timedelta(hours=1)) if r.status == RunStatus.FAILED)


@pytest.fixture
def inst():
    return DatabricksPlugin().build("dbx", CONFIG, {"token": FAKE_TOKEN}, client=fake_client())


@pytest.mark.parametrize(
    "life,result,expected",
    [
        ("PENDING", None, RunStatus.QUEUED),
        ("RUNNING", None, RunStatus.RUNNING),
        ("TERMINATED", "SUCCESS", RunStatus.SUCCEEDED),
        ("TERMINATED", "FAILED", RunStatus.FAILED),
        ("TERMINATED", "TIMEDOUT", RunStatus.FAILED),
        ("TERMINATED", "CANCELED", RunStatus.CANCELLED),
        ("INTERNAL_ERROR", None, RunStatus.FAILED),
        ("SKIPPED", None, RunStatus.SKIPPED),
    ],
)
def test_status_mapping(life, result, expected):
    assert map_status(life, result) == expected


def test_env_prefixes_and_include_filter(inst):
    pipelines = {p.pipeline_id: p for p in inst.run_source.list_pipelines()}
    assert set(pipelines) == {"1", "2", "3"}, "scratch_job is excluded"
    assert (pipelines["1"].name, pipelines["1"].env) == ("orders_pipeline", Env.PROD)
    assert (pipelines["2"].name, pipelines["2"].env) == ("orders_pipeline", Env.DEV)
    assert pipelines["3"].env == Env.QA
    assert pipelines["1"].tags == {"team": "data"}


def test_list_runs_merges_active_and_recent(inst):
    runs = {r.run_id: r for r in inst.run_source.list_runs(NOW - timedelta(hours=1))}
    assert set(runs) == {"10", "11"}
    assert runs["10"].status == RunStatus.RUNNING and runs["10"].parameters == {"sleep_seconds": "900"}
    assert runs["11"].status == RunStatus.FAILED and runs["11"].end_time is not None


def test_failure_context_reads_failed_task_output(inst):
    failed = next(r for r in inst.run_source.list_runs(NOW - timedelta(hours=1)) if r.run_id == "11")
    ctx = inst.logs.failure_context(failed)
    assert [t.task_key for t in ctx.tasks] == ["transform_silver"]
    assert ctx.error_class == ErrorClass.CODE and ctx.matched_rule == "cast_invalid_input"
    # Headline is the task's real error, not the generic run-level message.
    assert ctx.error_message.startswith("[CAST_INVALID_INPUT]")


def test_cancel_and_rerun_pass_ids_and_parameters(inst):
    backend = inst.backend
    active = inst.run_source.list_runs(NOW, active_only=True)[0]
    inst.controller.cancel(active)
    pipeline = next(p for p in inst.run_source.list_pipelines() if p.pipeline_id == "3")
    ref = inst.controller.rerun(pipeline, active.parameters)
    assert backend.client.jobs.cancelled == [10]
    assert backend.client.jobs.started == [(3, {"sleep_seconds": "900"})]
    assert ref.run_id == "999"


def test_code_ref_maps_env_to_branch(inst):
    pipelines = {p.pipeline_id: p for p in inst.run_source.list_pipelines()}
    assert inst.code.code_ref(pipelines["1"]).ref == "main"
    assert inst.code.code_ref(pipelines["2"]).ref == "dev"
    assert inst.code.code_ref(pipelines["1"]).path == "sample_pipelines/src"


def test_estimated_costs(inst):
    [rec] = inst.costs.costs(NOW - timedelta(hours=1))
    assert rec.is_estimate and rec.unit == "DBU" and rec.pipeline_id == "1"


def test_billing_table_costs():
    client = fake_client()
    client.statement_execution = NS(
        execute_statement=lambda **kw: NS(
            status=NS(state=NS(value="SUCCEEDED")),
            result=NS(data_array=[["1", "11", "2026-10-23", "0.75"]]),
        )
    )
    cfg = {**CONFIG, "use_billing_table": True, "warehouse_id": "wh1", "dbu_rate_usd": 0.5}
    inst = DatabricksPlugin().build("dbx", cfg, {"token": FAKE_TOKEN}, client=client)
    [rec] = inst.costs.costs(NOW - timedelta(days=1))
    assert rec.quantity == 0.75 and rec.estimated_cost_usd == 0.375 and not rec.is_estimate
    assert rec.env == Env.PROD


def test_healthcheck(inst):
    assert inst.healthcheck().startswith("ok")


# --- ActionProvider (data plane) -------------------------------------------------------


def action_client():
    client = fake_client()
    jobs = client.jobs
    jobs.repaired, jobs.submitted = [], []
    jobs.repair_run = lambda run_id, rerun_all_failed_tasks: (jobs.repaired.append((run_id, rerun_all_failed_tasks)), NS(response=NS(repair_id=55)))[1]
    jobs.submit = lambda **kw: (jobs.submitted.append(kw), NS(response=NS(run_id=777)))[1]
    jobs.get = lambda job_id: NS(settings=NS(as_dict=lambda: {"name": "orders_pipeline", "tasks": [{"task_key": "a"}]}))
    heads = {"head": "old0000"}
    client.repos = NS(
        updates=[],
        get=lambda repo_id: NS(branch="main", head_commit_id=heads["head"]),
    )

    def update(repo_id, branch):
        client.repos.updates.append((repo_id, branch))
        heads["head"] = "new1111"

    client.repos.update = update
    client.workspace = NS(get_status=lambda path: NS(object_id=4242, object_type=NS(value="DIRECTORY")))
    client.statements = []
    client.statement_execution = NS(
        execute_statement=lambda **kw: (
            client.statements.append(kw["statement"]),
            NS(status=NS(state=NS(value="SUCCEEDED")), result=NS(data_array=[["120", "1", "0", "2"]])),
        )[1]
    )
    return client


ACTION_CONFIG = {
    **CONFIG, "warehouse_id": "wh1", "git_url": "https://github.com/org/orders-pipelines",
    "git_folders": {"prod": "/Workspace/Shared/orders-pipelines_prod"},
}


@pytest.fixture
def act():
    return DatabricksPlugin().build("dbx", ACTION_CONFIG, {"token": FAKE_TOKEN}, client=action_client())


def test_actions_capability_and_get_run(act):
    from pipeline_agent.sdk import Capability

    assert act.supports(Capability.ACTIONS)
    assert act.actions.get_run("11").status == RunStatus.FAILED


def test_repair_reruns_failed_tasks_only(act):
    failed = act.actions.get_run("11")
    out = act.actions.repair(failed)
    assert act.backend.client.jobs.repaired == [(11, True)]
    assert out == {"run_id": "11", "repair_id": "55", "pipeline_id": "1"}


def test_sync_code_pulls_the_git_folder_found_by_path(act):
    out = act.actions.sync_code("prod")
    assert act.backend.client.repos.updates == [(4242, "main")]
    assert (out["head_before"], out["head"], out["branch"]) == ("old0000", "new1111", "main")
    with pytest.raises(ValueError, match="no Git folder"):
        act.actions.sync_code("qa")


def test_run_tests_submits_a_sequential_git_source_run(act):
    from databricks.sdk.service.jobs import GitProvider, RunIf, Source

    out = act.actions.run_tests("fix/incident-1", ["tests/test_transform_silver.py", "tests/test_build_gold"], {"catalog": "dev"})
    [kw] = act.backend.client.jobs.submitted
    assert out["run_id"] == "777"
    assert kw["git_source"].git_branch == "fix/incident-1" and kw["git_source"].git_provider == GitProvider.GIT_HUB
    first, second = kw["tasks"]
    assert first.notebook_task.notebook_path == "tests/test_transform_silver" and first.notebook_task.source == Source.GIT
    assert first.depends_on is None
    assert second.depends_on[0].task_key == first.task_key and second.run_if == RunIf.ALL_DONE


def test_profile_counts_and_rejects_bad_identifiers(act):
    out = act.actions.profile("prod.silver.orders", ["order_id"], ["store_id", "qty"])
    assert out == {"table": "prod.silver.orders", "rows": 120, "duplicate_keys": 1, "nulls": {"store_id": 0, "qty": 2}}
    assert "COUNT(DISTINCT order_id)" in act.backend.client.statements[0]
    with pytest.raises(ValueError, match="invalid identifier"):
        act.actions.profile("prod.silver.orders; DROP TABLE x", [], [])


def test_definition_hash_is_stable(act):
    assert act.actions.definition_hash("1") == act.actions.definition_hash("1")
