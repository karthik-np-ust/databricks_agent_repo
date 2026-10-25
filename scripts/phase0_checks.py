"""Phase 0 feasibility checks against the real Databricks workspace and OpenRouter.

Read-only except --create-catalog, which runs CREATE CATALOG IF NOT EXISTS dev
(the same thing setup_workspace.py does).

    python scripts/phase0_checks.py
    python scripts/phase0_checks.py --create-catalog
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.request

from _common import client, sql

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create-catalog", action="store_true")
    parser.add_argument("--model", help="OpenRouter model for the tool-calling check (default: first free model with tools)")
    args = parser.parse_args()
    w = client()

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
