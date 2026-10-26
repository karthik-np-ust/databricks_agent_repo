"""Phase 3 fix flow: patch validation and the full dev -> qa -> human -> prod state machine."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from pipeline_agent.config import Budgets, FixConfig, LlmConfig, PipelinePolicy, Settings
from pipeline_agent.fix import CodeContext, FixFlow, FixProposal, FixState, validate_proposal
from pipeline_agent.git.github import ChecksStatus, PullRequest, WorkflowRun
from pipeline_agent.llm import LLMGateway, Triage

SRC = "sample_pipelines/src/transform_silver.py"
TEST = "sample_pipelines/tests/test_transforms.py"
ORIGINAL_SRC = 'from common.dq import assert_not_null\n\ndef clean(df):\n    assert_not_null(df, ["order_id"])\n    return df.select(F.col("qty").cast("int"))\n'
FIXED_SRC = 'from common.dq import assert_not_null\n\ndef clean(df):\n    assert_not_null(df, ["order_id"])\n    return df.select(F.expr("try_cast(qty AS INT)"))\n'
ORIGINAL_TEST = "def test_ok():\n    assert True\n"
FIXED_TEST = ORIGINAL_TEST + "\ndef test_bad_qty_is_null():\n    assert True\n"

CFG = FixConfig(
    repo="o/r",
    required_checks=["agent-tests"],
    allowed_paths=["sample_pipelines/src/", "sample_pipelines/tests/"],
    protected_patterns=["assert_not_null("],
)


def proposal(files=None):
    return {
        "summary": "Use try_cast for qty",
        "files": files or [{"path": SRC, "content": FIXED_SRC}, {"path": TEST, "content": FIXED_TEST}],
        "risk": "low",
        "test_plan": "bad qty becomes null",
    }


# validation -------------------------------------------------------------------------
CTX = CodeContext(files={SRC: ORIGINAL_SRC, TEST: ORIGINAL_TEST}, allowed_paths=CFG.allowed_paths)


def errors_for(files):
    return validate_proposal(FixProposal.model_validate(proposal(files)), CTX, CFG)


def test_valid_fix_passes():
    assert errors_for(None) == []


@pytest.mark.parametrize(
    "files,expected",
    [
        ([{"path": ".github/workflows/deploy.yml", "content": "x"}, {"path": TEST, "content": FIXED_TEST}], "outside allowed paths"),
        ([{"path": "sample_pipelines/src/../../config/x.py", "content": "x=1"}, {"path": TEST, "content": FIXED_TEST}], "invalid path"),
        ([{"path": SRC, "content": "def broken(:\n"}, {"path": TEST, "content": FIXED_TEST}], "does not compile"),
        ([{"path": SRC, "content": "def clean(df):\n    return df\n"}, {"path": TEST, "content": FIXED_TEST}], "removes protected check"),
        ([{"path": SRC, "content": FIXED_SRC}], "no test added"),
        ([{"path": TEST, "content": FIXED_TEST}], "no source file changed"),
        ([{"path": SRC, "content": ORIGINAL_SRC}, {"path": TEST, "content": ORIGINAL_TEST}], "no file actually changes"),
        ([{"path": SRC, "content": FIXED_SRC + "\nTOKEN = 'dapiTESTnotARealTokenXXXXXXXXXXXXXX'\n"}, {"path": TEST, "content": FIXED_TEST}], "secret-like"),
    ],
)
def test_guards_reject_bad_fixes(files, expected):
    assert any(expected in e for e in errors_for(files)), errors_for(files)


# fakes ------------------------------------------------------------------------------
class FakeRepo:
    """In-memory GitHub: branches, commits, PRs, checks and deploy runs."""

    def __init__(self, protected=("main",)):
        self.files = {"base": {SRC: ORIGINAL_SRC, TEST: ORIGINAL_TEST, "README.md": "x"}}
        self.branches = {"main": "base", "dev": "base", "qa": "base"}
        self.prs: dict[int, dict] = {}
        self.comments: list[tuple[int, str]] = []
        self.check_state = "success"
        self.runs: dict[tuple[str, str], WorkflowRun] = {}
        self.protected = set(protected)
        self.auto_deploy = True  # deploy run appears (successful) for every merge

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

    def _do_merge(self, n):
        p = self.prs[n]
        sha = f"m{n}"
        self.files[sha] = {**self.files[self.branches[p["base"]]], **self.files[self.branches[p["head"]]]}
        self.branches[p["base"]] = sha
        p.update(state="closed", merged=True, merge_sha=sha)
        if self.auto_deploy:
            self.runs[(p["base"], sha)] = WorkflowRun(n, "completed", "success", f"https://gh/run/{n}")
        return sha

    def merge_pr(self, n, message):
        if self.prs[n]["base"] in self.protected:
            return None
        return self._do_merge(n)

    def human_merge(self, base):
        n = next(n for n, p in self.prs.items() if p["base"] == base and p["state"] == "open")
        return self._do_merge(n)

    def human_close(self, base):
        n = next(n for n, p in self.prs.items() if p["base"] == base and p["state"] == "open")
        self.prs[n]["state"] = "closed"

    def comment(self, n, body):
        self.comments.append((n, body))

    def checks(self, sha, required):
        failures = ["agent-tests: test_bad_qty_is_null failed"] if self.check_state == "failure" else []
        return ChecksStatus(self.check_state, {}, failures)

    def workflow_run(self, workflow, branch, sha):
        return self.runs.get((branch, sha))


DIAG = {
    "summary": "qty 'abc' breaks cast", "root_cause": "strict cast", "error_class": "code", "confidence": 0.9,
    "fix_type": "code_change", "suggested_fix": "try_cast", "suspicious_content": False,
}


class FakeLLM:
    """Returns a diagnosis or a fix depending on which tool is offered."""

    def __init__(self, fixes):
        self.fixes = list(fixes)
        self.chat = NS(completions=NS(create=self._create))
        self.fix_calls = 0

    def _create(self, **kw):
        tool = kw["tools"][0]["function"]["name"]
        if tool == "propose_fix":
            self.fix_calls += 1
            args = self.fixes.pop(0)
        else:
            args = DIAG
        call = NS(function=NS(name=tool, arguments=json.dumps(args)))
        return NS(model="fake", choices=[NS(message=NS(content="", tool_calls=[call]))], usage=NS(prompt_tokens=10, completion_tokens=10, cost=0.0))


@pytest.fixture
def setup(make_monitor, clock, store):
    def _setup(fixes=None, repo=None, settings=None, pipelines=None):
        monitor, sim, notifier = make_monitor(
            pipelines or [{"name": "orders_pipeline", "behaviour": "fail_code", "code_path": "sample_pipelines/src"}],
            policies=[PipelinePolicy(match="orders_*", auto_fix=True)],
            settings=settings,
        )
        llm = FakeLLM(fixes or [proposal()])
        gw = LLMGateway(llm, store, Budgets(), LlmConfig(triage_models=["t"], fix_models=["f"]), clock=clock)
        repo = repo or FakeRepo()
        monitor.triage = Triage(gw)
        monitor.fixer = FixFlow(monitor.settings, CFG, store, repo, gw, ["f"], monitor.instances, notifier, clock=clock)
        return monitor, sim, repo, llm, notifier

    return _setup


def tick(monitor, clock, seconds=61):
    clock.advance(seconds)
    return monitor.tick()


def fix_state(store):
    return store.list_fixes()[-1].state


# full flow ----------------------------------------------------------------------------
def test_full_flow_dev_qa_human_gate_prod_rerun_resolved(setup, clock, store):
    monitor, sim, repo, llm, notifier = setup()
    monitor.tick()
    tick(monitor, clock)  # failure -> incident -> diagnosis -> fix started -> PLANNED -> PR to dev
    assert fix_state(store) == "pr_open"
    dev_pr = repo.prs[1]
    assert (dev_pr["head"], dev_pr["base"]) == ("fix/incident-1", "dev")
    assert "Automated fix for incident #1" in dev_pr["body"] and "try_cast" in dev_pr["body"]
    assert repo.files[repo.branches["fix/incident-1"]][SRC] == FIXED_SRC

    tick(monitor, clock)  # CI green -> agent merges into dev
    assert fix_state(store) == "deploying"
    tick(monitor, clock)  # dev deploy green -> PR dev -> qa
    assert fix_state(store) == "pr_open" and repo.prs[2]["base"] == "qa"
    tick(monitor, clock)  # merge qa
    tick(monitor, clock)  # qa deploy green -> PR qa -> main
    assert repo.prs[3]["base"] == "main" and "Human approval required" in repo.prs[3]["body"]

    tick(monitor, clock)  # CI green on main PR -> awaiting approval (agent must NOT merge)
    tick(monitor, clock)
    assert repo.prs[3]["state"] == "open"
    approvals = [n for n in notifier.sent if n.title.startswith("Approval needed")]
    assert len(approvals) == 1, "approval request is sent once"

    repo.human_merge("main")  # the human gate
    sim._by_name["orders_pipeline"].behaviour = "ok"  # the fix works in prod
    tick(monitor, clock)  # merged -> deploying prod
    tick(monitor, clock)  # prod deploy green -> rerun prod
    assert fix_state(store) == "verifying"
    tick(monitor, clock)  # rerun succeeded
    assert fix_state(store) == "resolved"
    [inc] = store.list_incidents()
    assert inc.status == "resolved"
    fix = store.list_fixes()[0]
    assert set(fix.prs) == {"dev", "qa", "main"}
    assert sim.runs[-1]["triggered_by"] == "agent"
    actions = [r.action for r in store.list_audit()]
    for expected in ("fix_started", "fix_pr_opened", "fix_pr_merged", "fix_awaiting_approval", "fix_prod_rerun", "fix_resolved"):
        assert expected in actions


def test_invalid_patch_gets_one_retry(setup, clock, store):
    bad = proposal([{"path": SRC, "content": "def clean(df):\n    return df\n"}, {"path": TEST, "content": FIXED_TEST}])
    monitor, _, repo, llm, _ = setup(fixes=[bad, proposal()])
    monitor.tick()
    tick(monitor, clock)
    assert llm.fix_calls == 2 and fix_state(store) == "pr_open"


def test_no_valid_patch_escalates(setup, clock, store):
    bad = proposal([{"path": ".github/workflows/deploy.yml", "content": "pwned"}, {"path": TEST, "content": FIXED_TEST}])
    monitor, _, repo, _, _ = setup(fixes=[bad, bad])
    monitor.tick()
    tick(monitor, clock)
    assert fix_state(store) == "failed" and "outside allowed paths" in store.list_fixes()[0].error
    assert repo.prs == {} and store.list_incidents()[0].status == "escalated"


def test_ci_failure_comments_and_escalates(setup, clock, store):
    monitor, _, repo, _, _ = setup()
    repo.check_state = "failure"
    monitor.tick()
    tick(monitor, clock)
    tick(monitor, clock)
    assert fix_state(store) == "failed"
    assert repo.comments and "test_bad_qty_is_null failed" in repo.comments[0][1]
    assert repo.prs[1]["state"] == "open", "the PR stays open for a human"


def test_reviewer_rejects_final_pr(setup, clock, store):
    monitor, _, repo, _, _ = setup()
    monitor.tick()
    for _ in range(6):
        tick(monitor, clock)
    repo.human_close("main")
    tick(monitor, clock)
    assert fix_state(store) == "rejected" and store.list_incidents()[0].status == "escalated"


def test_protected_qa_waits_for_human_merge(setup, clock, store):
    monitor, _, repo, _, _ = setup(repo=FakeRepo(protected=("main", "qa")))
    monitor.tick()
    for _ in range(4):
        tick(monitor, clock)
    assert fix_state(store) == "pr_open" and repo.prs[2]["state"] == "open"
    assert "fix_merge_blocked" in [r.action for r in store.list_audit()]
    repo.human_merge("qa")
    tick(monitor, clock)
    assert fix_state(store) == "deploying"


def test_deploy_failure_fails_fix(setup, clock, store):
    repo = FakeRepo()
    repo.auto_deploy = False
    monitor, _, repo, _, _ = setup(repo=repo)
    monitor.tick()
    tick(monitor, clock)
    tick(monitor, clock)  # merged into dev, waiting for deploy run
    repo.runs[("dev", "m1")] = WorkflowRun(9, "completed", "failure", "https://gh/run/9")
    tick(monitor, clock)
    assert fix_state(store) == "failed" and "deploy to dev failure" in store.list_fixes()[0].error


def test_stuck_fix_times_out(setup, clock, store):
    repo = FakeRepo()
    repo.check_state = "pending"
    monitor, _, repo, _, _ = setup(repo=repo)
    monitor.tick()
    tick(monitor, clock)
    tick(monitor, clock, seconds=49 * 3600)
    assert fix_state(store) == "failed" and "no progress" in store.list_fixes()[0].error


def test_dry_run_resolves_without_prod_rerun(setup, clock, store):
    monitor, sim, repo, _, _ = setup(settings=Settings(_env_file=None, dry_run=True))
    monitor.tick()
    for _ in range(6):
        tick(monitor, clock)
    repo.human_merge("main")
    tick(monitor, clock)
    tick(monitor, clock)
    fix = store.list_fixes()[0]
    assert fix.state == "resolved" and "rerun not sent (dry run)" in fix.history[-1]["note"]
    assert all(r["triggered_by"] != "agent" for r in sim.runs)


def test_only_one_fix_in_flight(setup, clock, store):
    pipelines = [
        {"name": "orders_pipeline", "behaviour": "fail_code", "code_path": "sample_pipelines/src"},
        {"name": "orders_pipeline_2", "behaviour": "fail_code", "code_path": "sample_pipelines/src"},
    ]
    monitor, _, _, _, _ = setup(pipelines=pipelines)
    monitor.tick()
    tick(monitor, clock)
    assert len(store.list_fixes()) == 1
    assert "fix_deferred" in [r.action for r in store.list_audit()]
