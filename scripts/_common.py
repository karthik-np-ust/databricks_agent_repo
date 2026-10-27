"""Shared helpers for the demo scripts.

Auth uses the standard Databricks environment variables (DATABRICKS_HOST,
DATABRICKS_TOKEN) or a ~/.databrickscfg profile. SQL needs DATABRICKS_WAREHOUSE_ID
(Free Edition has one 2X-Small warehouse; copy its ID from SQL Warehouses).

The pipelines themselves live in their own repo (orders-pipelines); data seeding runs
there as notebooks, so nothing here uploads files (blocked by the corporate proxy).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

try:  # .env holds local credentials (git-ignored); real env vars take precedence
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:
    pass

try:  # trust the OS certificate store (corporate TLS inspection); see pipeline_agent/net.py
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

ENVS = ("dev", "qa", "prod")
JOB_PREFIX = {"dev": "[dev] ", "qa": "[qa] ", "prod": ""}
PIPELINES_REPO_URL = "https://github.com/karthik-np-ust/orders-pipelines"
# Databricks Git folders of the pipelines repo, one per environment (see docs/15-databricks-setup.md).
GIT_FOLDERS = {
    "dev": "/Workspace/Shared/orders-pipelines-dev",
    "qa": "/Workspace/Shared/orders-pipelines_qa",
    "prod": "/Workspace/Shared/orders-pipelines_prod",
}


def client():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def warehouse_id() -> str:
    wid = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not wid:
        sys.exit("Set DATABRICKS_WAREHOUSE_ID (SQL Warehouses -> your warehouse -> ID)")
    return wid


def sql(w, statement: str, check: bool = True) -> list[list[str]]:
    resp = w.statement_execution.execute_statement(statement=statement, warehouse_id=warehouse_id(), wait_timeout="50s")
    while resp.status and resp.status.state and resp.status.state.value in ("PENDING", "RUNNING"):
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)
    state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
    if state != "SUCCEEDED":
        message = resp.status.error.message if resp.status and resp.status.error else state
        if check:
            raise RuntimeError(f"SQL failed: {statement}\n  -> {message}")
        print(f"  ! {message}")
        return []
    return (resp.result.data_array if resp.result else None) or []


def find_job(w, base_name: str, env: str):
    full = JOB_PREFIX[env] + base_name
    for job in w.jobs.list(name=full):
        if job.settings and job.settings.name == full:
            return job
    sys.exit(f"Job {full!r} not found. Create it from the pipelines repo's jobs/{env}/{base_name}.json")


def run_job(w, base_name: str, env: str, params: dict[str, str] | None = None) -> str:
    job = find_job(w, base_name, env)
    waiter = w.jobs.run_now(job_id=job.job_id, job_parameters=params or None)
    run_id = waiter.response.run_id
    print(f"  started {JOB_PREFIX[env]}{base_name} run {run_id} params={params or {}}")
    return str(run_id)


def run_notebook(w, path: str, params: dict[str, str]) -> None:
    """One-time serverless run of a workspace notebook; waits and fails loudly."""
    from databricks.sdk.service.jobs import NotebookTask, SubmitTask

    print(f"  running {path} {params}")
    run = w.jobs.submit(
        run_name=f"demo: {path.rsplit('/', 1)[-1]}",
        tasks=[SubmitTask(task_key="notebook", notebook_task=NotebookTask(notebook_path=path, base_parameters=params))],
    ).result()
    state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
    if state != "SUCCESS":
        sys.exit(f"  notebook run {run.run_id} ended {state}: {run.run_page_url}")


def confirm(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if input(f"{prompt} [y/N] ").strip().lower() != "y":
        sys.exit("aborted")
