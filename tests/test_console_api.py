"""Console API: auth/RBAC, read views over a real (simulated) agent run, audited actions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline_agent.api.app import create_app
from pipeline_agent.api.context import ConsoleContext
from pipeline_agent.config import PipelinePolicy, Settings


@pytest.fixture
def console(make_monitor, clock, store):
    """A console over the same store the agent writes, after a few agent ticks."""
    monitor, sim, _ = make_monitor(
        [
            {"name": "orders_pipeline", "behaviour": "fail_code", "code_path": "sample_pipelines/src"},
            {"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 3600},
            {"name": "daily_kpis", "behaviour": "ok", "duration_seconds": 30},
        ],
        policies=[PipelinePolicy(match="orders_*", auto_fix=True)],
    )
    monitor.tick()
    clock.advance(61)
    monitor.tick()
    settings = Settings(_env_file=None)
    ctx = ConsoleContext(settings, config=monitor.config, store=store, instances=monitor.instances)
    client = TestClient(create_app(settings, ctx))
    return client, sim


def login(client, role="viewer", name="tester"):
    r = client.post("/api/v1/auth/demo-login", json={"name": name, "role": role})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_requires_login_and_rejects_tampered_token(console):
    client, _ = console
    assert client.get("/api/v1/overview").status_code == 401
    token = login(client)["Authorization"]
    assert client.get("/api/v1/overview", headers={"Authorization": token[:-2] + "xx"}).status_code == 401


def test_security_headers(console):
    client, _ = console
    r = client.get("/api/health")
    assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_overview_reflects_agent_state(console):
    client, _ = console
    data = client.get("/api/v1/overview", headers=login(client)).json()
    k = data["kpis"]
    assert k["pipelines"] == 3 and k["incidents_open"] == 1 and k["running_now"] == 1
    assert data["agent"]["platforms"][0]["id"] == "sim"
    assert data["activity"], "audit feed present"


def test_pipelines_incidents_and_detail(console):
    client, _ = console
    h = login(client)
    pipes = client.get("/api/v1/pipelines", headers=h).json()
    orders = next(p for p in pipes if p["name"] == "orders_pipeline")
    assert orders["last_status"] == "failed" and orders["open_incidents"] == 1 and orders["auto_fix"] is True
    [inc] = client.get("/api/v1/incidents", headers=h).json()
    detail = client.get(f"/api/v1/incidents/{inc['id']}", headers=h).json()
    assert detail["incident"]["matched_rule"] == "cast_invalid_input"
    assert any(a["action"] == "incident_opened" for a in detail["audit"])
    assert "CAST_INVALID_INPUT" in detail["error_text"]


def test_active_runs_show_threshold_progress(console):
    client, _ = console
    [run] = client.get("/api/v1/runs/active", headers=login(client)).json()
    assert run["pipeline_name"] == "sales_report" and run["threshold_s"] == 300 and run["pct_of_threshold"] >= 20


def test_rbac_viewer_cannot_act_operator_can_and_is_audited(console):
    client, sim = console
    viewer, operator = login(client, "viewer"), login(client, "operator", "karthik")
    body = {"platform_id": "sim", "pipeline_id": "daily_kpis", "reason": "manual rerun test"}
    assert client.post("/api/v1/pipelines/rerun", json=body, headers=viewer).status_code == 403
    r = client.post("/api/v1/pipelines/rerun", json=body, headers=operator)
    assert r.status_code == 200 and sim.runs[-1]["triggered_by"] == "agent"
    audit = client.get("/api/v1/audit?action=rerun", headers=viewer).json()
    assert audit[0]["actor"] == "user:karthik (operator)" and audit[0]["details"]["reason"] == "manual rerun test"


def test_reason_is_mandatory_for_actions(console):
    client, _ = console
    r = client.post("/api/v1/pipelines/rerun", json={"platform_id": "sim", "pipeline_id": "daily_kpis", "reason": ""},
                    headers=login(client, "operator"))
    assert r.status_code == 422


def test_admin_controls_kill_switch_reaches_agent(console, make_monitor, store):
    client, _ = console
    assert client.put("/api/v1/controls", json={"enabled": False, "reason": "maintenance"}, headers=login(client, "operator")).status_code == 403
    r = client.put("/api/v1/controls", json={"enabled": False, "reason": "maintenance window"}, headers=login(client, "admin"))
    assert r.status_code == 200
    assert store.get_controls()["enabled"] is False
    assert client.get("/api/v1/overview", headers=login(client)).json()["agent"]["enabled"] is False


def test_kill_switch_from_console_stops_agent(make_monitor, clock, store):
    monitor, sim, _ = make_monitor([{"name": "sales_report", "behaviour": "slow"}])
    store.set_control("enabled", False, actor="admin")
    clock.advance(400)
    assert monitor.tick().skipped and len(sim.runs) == 1
    store.set_control("enabled", True, actor="admin")
    assert not monitor.tick().skipped


def test_takeover_stops_agent_on_incident(console):
    client, _ = console
    h = login(client, "operator")
    [inc] = client.get("/api/v1/incidents", headers=h).json()
    assert client.post(f"/api/v1/incidents/{inc['id']}/takeover", json={"reason": "I'll handle this"}, headers=h).status_code == 200
    assert client.get(f"/api/v1/incidents/{inc['id']}", headers=h).json()["incident"]["status"] == "escalated"


def test_settings_never_expose_secret_values(console):
    client, _ = console
    data = client.get("/api/v1/settings", headers=login(client)).json()
    assert "plugins" in data and data["fix"]["promotion"] == ["dev", "qa", "main"]
    for p in data["platforms"]:
        assert all(v.endswith("://…") for v in p["secrets"].values())
