"""Code-fix path end to end: Recovery fix writer + Testing agent + promotion, over both planes.

In-memory GitHub (pipelines repo), fake LLM, simulator with one job per env. Every platform
change (tests, Git-folder syncs, smoke runs, prod repair) is a signed request the data plane
executes; GitHub work stays in the control plane; prod needs the human merge.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from pipeline_agent.autonomy import PolicyBundle, PolicyEngine
from pipeline_agent.config import AgentConfig, Budgets, FixConfig, LlmConfig, PipelinePolicy
from pipeline_agent.control_plane.agents.testing import TestProposal, validate_tests
from pipeline_agent.control_plane.deps import ControlPlaneDeps
from pipeline_agent.control_plane.registry import AgentRegistry
from pipeline_agent.control_plane.runner import ControlPlane
from pipeline_agent.data_plane.executor import Executor
from pipeline_agent.data_plane.observer import Observer
from pipeline_agent.data_plane.router import CapabilityRouter
from pipeline_agent.evidence import EvidenceStore, LocalBlobs
from pipeline_agent.git.github import ChecksStatus, PullRequest
from pipeline_agent.kedb import KnownErrorDB
from pipeline_agent.llm.gateway import LLMGateway
from pipeline_agent.llm.model_gateway import AliasedModelGateway
from pipeline_agent.notify.webhook import ConsoleNotifier
from pipeline_agent.security.signing import Signer, Verifier
from pipeline_agent.store.db import ActionRequestRow, FixAttemptRow, IncidentRow
from pipeline_agent_simulator import SimulatorPlugin

CONFIG = Path(__file__).resolve().parents[1] / "config"
NB = "notebooks/transform_silver.py"
HEADER = "# Databricks notebook source\nfrom pyspark.sql import SparkSession\nfrom pyspark.sql import functions as F\nfrom databricks.sdk.runtime import dbutils\n\nspark = SparkSession.builder.getOrCreate()\n"
DQ = 'bad = spark.read.table("t").filter(F.col("order_id").isNull()).count()\nif bad:\n    raise Exception(f"DQAssertionError: {bad} row(s) with null order_id")\n'
ORIGINAL = HEADER + "\n# COMMAND ----------\n\n" + DQ + 'orders = spark.read.table("t").select(F.col("qty").cast("int"))\n'
FIXED = HEADER + "\n# COMMAND ----------\n\n" + DQ + 'orders = spark.read.table("t").select(F.expr("try_cast(qty AS INT)").alias("qty"))\n'
TEST_NB = (
    "# Databricks notebook source\nimport uuid\n\nfrom pyspark.sql import SparkSession\nfrom databricks.sdk.runtime import dbutils\n\n"
    "spark = SparkSession.builder.getOrCreate()\ntest_env = 'test_' + uuid.uuid4().hex[:8]\n\n# COMMAND ----------\n\n"
    "try:\n    spark.sql(f'CREATE SCHEMA IF NOT EXISTS dev.{test_env}_silver')\n"
    "    dbutils.notebook.run('../notebooks/transform_silver', 600, {'catalog': 'dev', 'env': test_env, 'layout': 'schema'})\n"
    "    assert spark.read.table(f'dev.{test_env}_silver.orders').count() == 6\n"
    "finally:\n    spark.sql(f'DROP SCHEMA IF EXISTS dev.{test_env}_silver CASCADE')\n"
)
FIX = {"summary": "try_cast qty so bad values become null", "files": [{"path": NB, "content": FIXED}], "risk": "low", "test_plan": "bad qty row"}
REPAIRS = [
    {**FIX, "summary": f"repair {n}: also trim qty", "files": [{"path": NB, "content": FIXED.replace("try_cast(qty AS INT)", "try_cast(trim(qty) AS INT)" + " " * n)}]}
    for n in (1, 2)
]
TESTS = {"summary": "bad qty no longer fails silver", "files": [{"path": "tests/test_transform_silver_bad_qty.py", "content": TEST_NB}]}


class FakeRepo:
    """In-memory pipelines repo: branches, commits, PRs, checks. ``on_merge`` lets the sim's Git folders follow merges."""

    def __init__(self, on_merge=None):
        self.files = {"base": {NB: ORIGINAL, "tests/test_transform_silver.py": TEST_NB, "README.md": "x"}}
        self.branches = {"main": "base", "dev": "base", "qa": "base"}
        self.prs: dict[int, dict] = {}
        self.check_state = "success"
        self.on_merge = on_merge

    def branch_sha(self, branch):
        return self.branches[branch]

    def list_files(self, sha):
        return sorted(self.files[sha])

    def read_file(self, path, ref):
        return self.files[ref].get(path)

    def commit_files(self, base_sha, branch, files, message):
        sha = f"c{len(self.files)}"
        self.files[sha] = {**self.files[base_sha], **files}
        self.branches[branch] = sha
        return sha

    def _pr(self, n):
        p = self.prs[n]
        return PullRequest(n, f"https://gh/pr/{n}", self.branches[p["head"]], p["state"], p["merged"], p.get("merge_sha"))

    def open_pr(self, head, base, title, body):
        n = len(self.prs) + 1
        self.prs[n] = {"head": head, "base": base, "title": title, "body": body, "state": "open", "merged": False}
        return self._pr(n)

    def find_open_pr(self, head, base):
        return next((self._pr(n) for n, p in self.prs.items() if p["head"] == head and p["base"] == base and p["state"] == "open"), None)

    def get_pr(self, n):
        return self._pr(n)

    def merge_pr(self, n, message):
        if self.prs[n]["base"] == "main":
            return None  # branch protection: a human merges prod
        return self._merge(n)

    def _merge(self, n):
        p = self.prs[n]
        sha = f"m{n}000000"
        self.files[sha] = {**self.files[self.branches[p["base"]]], **self.files[self.branches[p["head"]]]}
        self.branches[p["base"]] = sha
        p.update(state="closed", merged=True, merge_sha=sha)
        if self.on_merge:
            self.on_merge(p["base"], sha)
        return sha

    def human(self, action, base="main"):
        n = next(n for n, p in self.prs.items() if p["base"] == base and p["state"] == "open")
        if action == "merge":
            return self._merge(n)
        self.prs[n]["state"] = "closed"

    def comment(self, n, body):
        pass

    def checks(self, sha, required):
        return ChecksStatus(self.check_state, {}, ["notebook-lint: failed"] if self.check_state == "failure" else [])


class FakeLLM:
    def __init__(self, fixes=None, tests=None, on_repair=None):
        self.on_repair = on_repair
        self.fixes, self.tests = list(fixes or [FIX, *REPAIRS]), list(tests or [TESTS, TESTS])
        self.calls = {"propose_fix": 0, "propose_tests": 0}
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        tool = kw["tools"][0]["function"]["name"]
        self.calls[tool] = self.calls.get(tool, 0) + 1
        if tool == "propose_fix" and self.calls[tool] > 1 and self.on_repair:
            self.on_repair()
        args = (self.fixes if tool == "propose_fix" else self.tests).pop(0)
        call = NS(function=NS(name=tool, arguments=json.dumps(args)))
        return NS(model="fake", choices=[NS(message=NS(content="", tool_calls=[call]))], usage=NS(prompt_tokens=10, completion_tokens=10, cost=0.0))


PIPELINES = [
    {"name": "orders_pipeline", "id": "orders_pipeline", "behaviour": "fail_code", "duration_seconds": 30},
    {"name": "orders_pipeline", "id": "dev_orders", "env": "dev", "behaviour": "ok", "duration_seconds": 30},
    {"name": "orders_pipeline", "id": "qa_orders", "env": "qa", "behaviour": "ok", "duration_seconds": 30},
]


class FixDemo:
    def __init__(self, clock, store, tmp_path, llm=None, auto_fix=True, test_outcomes=None):
        self.clock, self.store = clock, store
        self.instance = SimulatorPlugin().build("sim", {"pipelines": PIPELINES, "test_outcomes": test_outcomes or {}}, {}, clock=clock)
        self.sim = self.instance.backend
        self.repo = FakeRepo(on_merge=lambda branch, sha: self.sim.config.branch_heads.__setitem__("prod" if branch == "main" else branch, sha))
        self.llm = llm or FakeLLM()
        signer = Signer.generate()
        engine = PolicyEngine(PolicyBundle.load(CONFIG / "policy" / "autonomy.yaml"), AgentRegistry.load(CONFIG / "agents"))
        evidence = EvidenceStore(store, LocalBlobs(tmp_path))
        fix = FixConfig(repo="org/orders-pipelines", allowed_paths=["notebooks/", "tests/"], protected_patterns=["DQAssertionError"],
                        required_checks=["notebook-lint"], repair_attempts=2, patch_attempts=2)
        llm_cfg = LlmConfig(triage_models=["fake"], fix_models=["fake"])
        config = AgentConfig(defaults=PipelinePolicy(max_duration_seconds=3000), policies=[PipelinePolicy(match="orders_pipeline", auto_fix=auto_fix)],
                             fix=fix, llm=llm_cfg)
        KnownErrorDB(store).seed_all()
        controls = {"enabled": True, "dry_run": False}
        self.observer = Observer(config, {"sim": self.instance}, store, evidence, clock=clock)
        self.executor = Executor(store, CapabilityRouter({"sim": self.instance}), Verifier([signer.public_key]), engine, evidence,
                                 controls=lambda: controls, clock=clock)
        gateway = LLMGateway(self.llm, store, Budgets(), llm_cfg, clock=clock)
        self.deps = ControlPlaneDeps(config=config, store=store, engine=engine, registry=AgentRegistry.load(CONFIG / "agents"), signer=signer,
                                     evidence=evidence, notifier=ConsoleNotifier(), repo=self.repo, models=AliasedModelGateway(gateway, llm_cfg),
                                     controls=lambda: controls, clock=clock)
        self.control = ControlPlane(self.deps, InMemorySaver(), holder="cp", lease_ttl=timedelta(days=1))

    def cycle(self, seconds=60):
        self.clock.advance(seconds)
        self.observer.tick()
        self.control.tick()
        self.executor.drain()
        self.control.tick()

    def run_until(self, done, max_cycles=40, each=None):
        for _ in range(max_cycles):
            self.cycle()
            if each:
                each(self)
            if done(self):
                return True
        return False

    def incident(self) -> IncidentRow:
        with self.store.session() as s:
            return s.query(IncidentRow).filter(IncidentRow.env == "prod").first()

    def caps(self) -> list[tuple[str, str]]:
        with self.store.session() as s:
            return [(r.capability, r.env) for r in s.query(ActionRequestRow).order_by(ActionRequestRow.created_at).all()]

    def requests(self):
        with self.store.session() as s:
            return s.query(ActionRequestRow).order_by(ActionRequestRow.created_at).all()

    def audit(self) -> list[str]:
        return [a.action for a in reversed(self.store.list_audit(1000))]

    def waiting_for_human(self) -> bool:
        return any(p["base"] == "main" and p["state"] == "open" for p in self.repo.prs.values())


def test_fix_is_tested_in_databricks_promoted_and_repairs_prod(clock, store, tmp_path):
    demo = FixDemo(clock, store, tmp_path)
    assert demo.run_until(lambda d: d.waiting_for_human()), demo.audit()
    # Up to the human gate: tests ran on the fix branch, dev and qa were merged, synced and smoke-run.
    assert demo.caps() == [
        ("tests.run", "dev"), ("code.sync", "dev"), ("pipeline.smoke_run", "dev"), ("code.sync", "qa"), ("pipeline.smoke_run", "qa"),
    ]
    tests_req = demo.requests()[0]
    assert tests_req.payload["params"]["branch"] == "fix/incident-1" and tests_req.agent == "testing"
    assert demo.repo.files[demo.repo.branches["fix/incident-1"]][NB] == FIXED
    assert "tests/test_transform_silver_bad_qty.py" in demo.repo.files[demo.repo.branches["fix/incident-1"]]
    assert [p["base"] for p in demo.repo.prs.values()] == ["dev", "qa", "main"]
    assert "Passed in Databricks" in demo.repo.prs[1]["body"]
    assert demo.incident().status == "fixing"

    demo.repo.human("merge")
    assert demo.run_until(lambda d: d.incident().status == "resolved"), demo.audit()
    prod = [r for r in demo.requests() if r.env == "prod"]
    assert [r.capability for r in prod] == ["code.sync", "pipeline.repair"]
    assert all(r.payload["approval"]["kind"] == "github_merge" and r.level == 3 for r in prod)
    assert prod[1].payload["target"]["run_id"] == "1"  # the original failed prod run, repaired

    audit = demo.audit()
    for name in ("fix_written", "tests_written", "tests_passed", "fix_pr_opened", "fix_pr_merged", "fix_deployed", "fix_resolved", "kedb_proposed"):
        assert name in audit, name
    assert audit.count("fix_deployed") == 3 and audit.count("fix_pr_merged") == 3
    with store.session() as s:
        fix = s.query(FixAttemptRow).one()
    assert fix.state == "resolved" and set(fix.prs) == {"dev", "qa", "main"}
    [proposed] = KnownErrorDB(store).list(status="proposed")
    assert proposed.provenance == "incident:1" and proposed.runbook == "code_fix" and "try_cast" in proposed.fix_hint
    assert demo.llm.calls == {"propose_fix": 1, "propose_tests": 1}


def test_failing_tests_send_the_fix_back_to_recovery(clock, store, tmp_path):
    # The simulator fixes a test run's outcome when it is submitted, so flip it when the repair is written.
    llm = FakeLLM(on_repair=lambda: demo.sim.config.test_outcomes.__setitem__("fix/incident-1", "pass"))
    demo = FixDemo(clock, store, tmp_path, llm=llm, test_outcomes={"fix/incident-1": "fail"})
    assert demo.run_until(lambda d: d.waiting_for_human()), demo.audit()
    audit = demo.audit()
    assert audit.index("tests_failed") < audit.index("fix_repaired") < audit.index("tests_passed")
    assert [c for c, _ in demo.caps()].count("tests.run") == 2
    assert demo.llm.calls["propose_fix"] == 2  # fix + one repair fed with the Databricks test output
    assert len({r.idempotency_key for r in demo.requests()}) == len(demo.requests())


def test_failing_ci_on_the_fix_pr_is_repaired_on_the_same_pr(clock, store, tmp_path):
    llm = FakeLLM(on_repair=lambda: setattr(demo.repo, "check_state", "success"))
    demo = FixDemo(clock, store, tmp_path, llm=llm)
    demo.repo.check_state = "failure"
    assert demo.run_until(lambda d: d.waiting_for_human()), demo.audit()
    repaired = next(a for a in demo.store.list_audit(500) if a.action == "fix_repaired")
    assert repaired.details["source"] == "CI"
    assert [p["base"] for p in demo.repo.prs.values()] == ["dev", "qa", "main"]  # the repair reused PR #1
    assert [c for c, _ in demo.caps()].count("tests.run") == 2  # the repaired code was tested again in Databricks


def test_fix_escalates_when_repairs_are_exhausted(clock, store, tmp_path):
    demo = FixDemo(clock, store, tmp_path, test_outcomes={"fix/incident-1": "fail"})
    assert demo.run_until(lambda d: d.incident().status == "escalated"), demo.audit()
    assert demo.audit().count("fix_repaired") == 2
    assert demo.repo.prs == {}  # never promoted
    with store.session() as s:
        assert s.query(FixAttemptRow).one().state == "failed"


def test_closed_prod_pr_escalates_without_touching_prod(clock, store, tmp_path):
    demo = FixDemo(clock, store, tmp_path)
    assert demo.run_until(lambda d: d.waiting_for_human())
    demo.repo.human("close")
    assert demo.run_until(lambda d: d.incident().status == "escalated")
    assert not [c for c in demo.caps() if c[1] == "prod"]
    assert "closed without merging" in next(a.details["reason"] for a in demo.store.list_audit(500) if a.action == "incident_escalated")


def test_no_auto_fix_waits_for_a_human(clock, store, tmp_path):
    demo = FixDemo(clock, store, tmp_path, auto_fix=False)
    demo.run_until(lambda d: d.incident() is not None and d.incident().status != "fixing", max_cycles=3)
    assert demo.incident().status == "open" and "awaiting_code_fix" in demo.audit()
    assert demo.caps() == [] and demo.llm.calls["propose_fix"] == 0


@pytest.mark.parametrize(
    "path,content,expected",
    [
        ("notebooks/x.py", TEST_NB, "must be tests/test_"),
        ("tests/test_x.py", TEST_NB.replace("# Databricks notebook source\n", ""), "must start with"),
        ("tests/test_x.py", TEST_NB.replace("dbutils.notebook.run('../notebooks/transform_silver'", "print('x'"), "must run the pipeline notebook"),
        ("tests/test_x.py", TEST_NB.replace("DROP SCHEMA", "SELECT"), "must DROP"),
        ("tests/test_x.py", TEST_NB + "\nassert missing_name\n", "undefined name"),
        ("tests/test_x.py", TEST_NB + "\nTOKEN = 'dapiTESTnotARealTokenXXXXXXXXXXXXXX'\n", "secret-like"),
    ],
)
def test_testing_agent_guards(path, content, expected):
    errors = validate_tests(TestProposal(summary="s", files=[{"path": path, "content": content}]), [NB])
    assert any(expected in e for e in errors), errors


def test_valid_test_notebook_passes_the_guards():
    assert validate_tests(TestProposal(summary="s", files=[{"path": "tests/test_x.py", "content": TEST_NB}]), [NB]) == []
