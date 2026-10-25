"""Shared helpers for the demo scripts.

Auth uses the standard Databricks environment variables (DATABRICKS_HOST,
DATABRICKS_TOKEN) or a ~/.databrickscfg profile. SQL needs DATABRICKS_WAREHOUSE_ID
(Free Edition has one 2X-Small warehouse; copy its ID from SQL Warehouses).
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sample_pipelines" / "src"))

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

from common.config import Tables  # noqa: E402  (shared with the pipelines, so names never drift)

ENVS = ("dev", "qa", "prod")
JOB_PREFIX = {"dev": "[dev] ", "qa": "[qa] ", "prod": ""}


def tables(env: str, layout: str = "catalog") -> Tables:
    return Tables(catalog=env if layout == "catalog" else "workspace", env=env, layout=layout)


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
    sys.exit(f"Job {full!r} not found. Deploy the bundle first: databricks bundle deploy -t {env}")


def run_job(w, base_name: str, env: str, params: dict[str, str] | None = None) -> str:
    job = find_job(w, base_name, env)
    waiter = w.jobs.run_now(job_id=job.job_id, job_parameters=params or None)
    run_id = waiter.response.run_id
    print(f"  started {JOB_PREFIX[env]}{base_name} run {run_id} params={params or {}}")
    return str(run_id)


def replace_landing(w, env: str, csv_text: str, layout: str = "catalog", name: str = "orders.csv") -> None:
    """Replace the landing folder's contents so each scenario is deterministic."""
    landing = tables(env, layout).landing_path
    try:
        for entry in w.files.list_directory_contents(landing):
            if not entry.is_directory:
                w.files.delete(entry.path)
    except Exception:
        w.files.create_directory(landing)
    w.files.upload(f"{landing}/{name}", io.BytesIO(csv_text.encode("utf-8")), overwrite=True)
    print(f"  uploaded {name} to {landing}")


def confirm(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if input(f"{prompt} [y/N] ").strip().lower() != "y":
        sys.exit("aborted")
