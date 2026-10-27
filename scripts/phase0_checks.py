"""Phase 0 feasibility checks against the real Databricks workspace and OpenRouter.

Read-only except --create-catalog, which runs CREATE CATALOG IF NOT EXISTS dev
(the same thing the pipelines repo's setup/00_setup_demo notebook does).

--redesign runs only the checks for the LangGraph redesign (docs/04, phase R0): Git folders
(Repos API), test notebook runs from a GitHub branch and from the dev Git folder (each creates
and drops its own dev.test_* schemas), jobs.repair_run, SQL warehouse and system.lakeflow.

    python scripts/phase0_checks.py
    python scripts/phase0_checks.py --create-catalog
    python scripts/phase0_checks.py --redesign
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.request

from _common import GIT_FOLDERS, PIPELINES_REPO_URL, client, find_job, sql

OPENROUTER = "https://openrouter.ai/api/v1"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool | None, detail: str) -> None:
    status = "PASS" if ok else ("INFO" if ok is None else "FAIL")
    results.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def try_check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # each check is independent
        record(name, False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")


def openrouter(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        OPENROUTER + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def submit_test(w, name: str, task: dict, git_source: dict | None = None) -> None:
    """One-time serverless run of a test notebook; PASS only if the notebook (and its asserts) succeed."""
    from databricks.sdk.service.jobs import GitSource, SubmitTask

    run = w.jobs.submit(
        run_name=f"r0 check: {name}",
        git_source=GitSource.from_dict(git_source) if git_source else None,
        tasks=[SubmitTask.from_dict({"task_key": "test", **task})],
    ).result()
    state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
    message = (run.state.state_message or "") if run.state else ""
    record(name, state == "SUCCESS", f"{state} {message[:200]} {run.run_page_url}")


def redesign_checks(w) -> list:
    def git_folders():
        for env, path in GIT_FOLDERS.items():
            status = w.workspace.get_status(path)
            kind = status.object_type.value if status.object_type else "?"
            try:
                repo = w.repos.get(status.object_id)
                record(f"git folder {env}", True, f"{path}: {kind}, repo id {repo.id}, branch {repo.branch}, head {(repo.head_commit_id or '')[:8]}")
            except Exception as exc:
                record(f"git folder {env}", False, f"{path}: {kind}, not a Git folder for the Repos API ({type(exc).__name__}); code.sync can't pull it")

    def git_source_run():
        submit_test(
            w,
            "test run from GitHub branch (git_source)",
            {"notebook_task": {"notebook_path": "tests/test_transform_silver", "source": "GIT", "base_parameters": {"catalog": "dev"}}},
            {"git_url": PIPELINES_REPO_URL, "git_provider": "gitHub", "git_branch": "dev"},
        )

    def workspace_run():
        submit_test(
            w,
            "test run from dev Git folder (dbutils.notebook.run)",
            {"notebook_task": {"notebook_path": f"{GIT_FOLDERS['dev']}/tests/test_transform_silver", "source": "WORKSPACE", "base_parameters": {"catalog": "dev"}}},
        )

    def repair():
        job = find_job(w, "orders_pipeline", "dev")
        runs = list(w.jobs.list_runs(job_id=job.job_id, completed_only=True, limit=1))
        if not runs:
            record("jobs.repair_run", None, "no completed [dev] orders_pipeline run to try it on")
            return
        try:
            w.jobs.repair_run(run_id=runs[0].run_id, rerun_all_failed_tasks=True)
            record("jobs.repair_run", True, f"accepted for run {runs[0].run_id}")
        except Exception as exc:
            text = str(exc)
            reachable = any(k in text.lower() for k in ("repair", "succeeded", "success", "failed tasks", "state"))
            record("jobs.repair_run", True if reachable else False, f"API reachable, refused for a successful run: {text.splitlines()[0][:200]}")

    def warehouse():
        sql(w, "SELECT 1")
        record("sql warehouse", True, "statement execution works (data.profile)")

    def lakeflow():
        rows = sql(w, "SELECT COUNT(*), MAX(period_start_time) FROM system.lakeflow.job_run_timeline")
        record("system.lakeflow.job_run_timeline", True, f"readable: rows={rows[0][0]}, latest {rows[0][1]}")

    return [
        ("git folders", git_folders), ("git_source test run", git_source_run), ("workspace test run", workspace_run),
        ("jobs.repair_run", repair), ("sql warehouse", warehouse), ("system.lakeflow", lakeflow),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create-catalog", action="store_true")
    parser.add_argument("--redesign", action="store_true", help="only the LangGraph redesign checks (R0)")
    parser.add_argument("--model", help="OpenRouter model for the tool-calling check (default: first free model with tools)")
    args = parser.parse_args()
    w = client()

    if args.redesign:
        for name, fn in redesign_checks(w):
            try_check(name, fn)
        print("\nSummary")
        for name, status, detail in results:
            print(f"  {status:<4} {name}")
        return

    def auth():
        w.current_user.me()
        record("databricks auth", True, "token valid")

    def jobs():
        n = sum(1 for _ in w.jobs.list(limit=100))
        record("jobs api", True, f"{n} job(s) visible")

    def billing():
        rows = sql(w, "SELECT COUNT(*), MIN(usage_date), MAX(usage_date) FROM system.billing.usage")
        record("system.billing.usage", True, f"readable: rows={rows[0][0]}, dates {rows[0][1]}..{rows[0][2]}")

    def catalogs():
        names = [c.name for c in w.catalogs.list()]
        record("catalogs visible", None, ", ".join(sorted(names)))
        if args.create_catalog:
            sql(w, "CREATE CATALOG IF NOT EXISTS dev")
            record("CREATE CATALOG", True, "catalog 'dev' exists/created -> use layout=catalog")

    def service_principals():
        sps = list(w.service_principals.list())
        record("workspace service principals API", True, f"{len(sps)} visible")

    def secrets():
        scopes = [s.name for s in w.secrets.list_scopes()]
        record("secret scopes API", True, f"{len(scopes)} scope(s)")

    def cli():
        exe = shutil.which("databricks")
        if not exe:
            record("databricks CLI", False, "not on PATH (needed for bundle deploy)")
            return
        out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
        record("databricks CLI", True, out)

    def or_key():
        data = openrouter("/key")["data"]
        free = data.get("free_model_daily_requests") or {}
        record(
            "openrouter key",
            True,
            f"limit={data.get('limit')} remaining={data.get('limit_remaining')} usage_daily={data.get('usage_daily')} "
            f"free_tier={data.get('is_free_tier')} free_requests={free}",
        )

    def or_tools():
        model = args.model
        if not model:
            models = openrouter("/models")["data"]
            free = [m["id"] for m in models if m["id"].endswith(":free") and "tools" in (m.get("supported_parameters") or [])]
            record("openrouter free models with tools", None, ", ".join(free[:8]) + (" ..." if len(free) > 8 else ""))
            model = free[0]
        resp = openrouter(
            "/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Classify this Databricks error with the tool: [CAST_INVALID_INPUT] value 'abc' cannot be cast to INT"}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "classify_error",
                        "description": "Classify a pipeline failure",
                        "parameters": {"type": "object", "properties": {"error_class": {"type": "string", "enum": ["transient", "code", "infra", "unknown"]}}, "required": ["error_class"]},
                    },
                }],
                "max_tokens": 200,
            },
        )
        msg = resp["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        usage = resp.get("usage", {})
        ok = bool(calls)
        detail = f"model={resp.get('model')} tool_call={calls[0]['function']['arguments'] if calls else 'none'} usage={usage}"
        record("openrouter tool calling", ok, detail)

    for name, fn in [
        ("databricks auth", auth), ("jobs api", jobs), ("system.billing.usage", billing), ("catalogs", catalogs),
        ("service principals", service_principals), ("secret scopes", secrets), ("databricks CLI", cli),
        ("openrouter key", or_key), ("openrouter tool calling", or_tools),
    ]:
        try_check(name, fn)

    print("\nSummary")
    for name, status, detail in results:
        print(f"  {status:<4} {name}")


if __name__ == "__main__":
    main()
