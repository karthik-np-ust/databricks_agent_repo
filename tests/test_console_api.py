"""Console API over both planes: auth/RBAC, read views, and actions that become signed, policy-checked requests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline_agent.api.app import create_app
from pipeline_agent.api.context import ConsoleContext
from pipeline_agent.config import PipelinePolicy, Settings

from test_control_plane import Demo

TOKEN = "dapiFAKE00000000000000000000000000"


@pytest.fixture
def console(clock, store, tmp_path):
    """A console over the store both planes write, after the agent saw a code failure, a slow run and a permission error."""
    demo = Demo(
        clock, store, tmp_path,
        [
            {"name": "orders_pipeline", "behaviour": "fail_code", "duration_seconds": 30},
            {"name": "sales_report", "behaviour": "slow", "slow_duration_seconds": 3600},
            {"name": "daily_kpis", "behaviour": "ok", "duration_seconds": 30},
            {"name": "customer_export", "behaviour": "fail_permission", "duration_seconds": 30,
             "error_message": f"[PERMISSION_DENIED] token {TOKEN} rejected"},
        ],
        policies=[PipelinePolicy(match="orders_*", auto_fix=True)],
    )
    demo.cycle(31)
    demo.cycle(301)  # sales_report passes its 300s threshold -> cancel needs approval
    settings = Settings(_env_file=None, evidence_dir=tmp_path)
    ctx = ConsoleContext(settings, config=demo.deps.config, store=store)  # no platform access: snapshots only
    return TestClient(create_app(settings, ctx)), demo


def login(client, role="viewer", name="tester"):
    r = client.post("/api/v1/auth/demo-login", json={"name": name, "role": role})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def incident(client, pipeline):
    return next(i for i in client.get("/api/v1/incidents", headers=login(client)).json() if i["pipeline_name"] == pipeline)


def test_requires_login_and_rejects_tampered_token(console):
    client, _ = console
    assert client.get("/api/v1/overview").status_code == 401
    assert client.get("/api/v1/action-approvals").status_code == 401
    token = login(client)["Authorization"]
    assert client.get("/api/v1/overview", headers={"Authorization": token[:-2] + "xx"}).status_code == 401


def test_security_headers(console):
    client, _ = console
    r = client.get("/api/health")
    assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_overview_reflects_both_planes(console):
    client, _ = console
    data = client.get("/api/v1/overview", headers=login(client)).json()
    k = data["kpis"]
    assert k["pipelines"] == 4 and k["running_now"] == 1 and k["approvals_pending"] == 1
    assert k["incidents_open"] == 2 and k["incidents_escalated"] == 1  # code fix + slow run open; permission escalated
    assert data["agent"]["last_tick"], "control-plane heartbeat"
    assert data["activity"], "audit feed present"


def test_incident_detail_has_evidence_requests_and_graph_state(console):
    client, _ = console
    h = login(client)
    orders = incident(client, "orders_pipeline")
    detail = client.get(f"/api/v1/incidents/{orders['id']}", headers=h).json()
    assert detail["incident"]["matched_rule"] == "cast_invalid_input" and "CAST_INVALID_INPUT" in detail["error_text"]
    assert [w["status"] for w in detail["work_items"]] == ["waiting_fix"]
    assert detail["evidence"][0]["kind"] == "failure_context"
    for action in ("incident_opened", "planned", "awaiting_code_fix"):
        assert any(a["action"] == action for a in detail["audit"]), action

    slow = client.get(f"/api/v1/incidents/{incident(client, 'sales_report')['id']}", headers=h).json()
    assert slow["work_items"][0]["waiting_for"] == "approval" and slow["approvals"][0]["status"] == "pending"


def test_evidence_is_served_redacted(console):
    client, _ = console
    h = login(client)
    detail = client.get(f"/api/v1/incidents/{incident(client, 'customer_export')['id']}", headers=h).json()
    ev = client.get(f"/api/v1/evidence/{detail['evidence'][0]['id']}", headers=h).json()
    assert ev["intact"] and "PERMISSION_DENIED" in ev["text"] and TOKEN not in ev["text"] and ev["redactions"] >= 1


def test_active_runs_show_threshold_progress(console):
    client, _ = console
    [run] = client.get("/api/v1/runs/active", headers=login(client)).json()
    assert run["pipeline_name"] == "sales_report" and run["threshold_s"] == 300 and run["pct_of_threshold"] >= 100


def test_operator_rerun_becomes_a_signed_request_the_data_plane_executes(console):
    client, demo = console
    viewer, operator = login(client, "viewer"), login(client, "operator", "karthik")
    body = {"platform_id": "sim", "pipeline_id": "daily_kpis", "reason": "manual rerun test"}
    assert client.post("/api/v1/pipelines/rerun", json=body, headers=viewer).status_code == 403
    runs_before = len(demo.sim.runs)
    r = client.post("/api/v1/pipelines/rerun", json=body, headers=operator)
    assert r.status_code == 200 and r.json()["queued"] is True
    assert len(demo.sim.runs) == runs_before  # the console itself changes nothing

    demo.cycle(1)
    [req] = [q for q in client.get("/api/v1/requests", headers=viewer).json() if q["capability"] == "pipeline.rerun"]
    assert req["agent"] == "console" and req["level"] == 3 and req["approval"] == {"kind": "console", "ref": "user:karthik", "by": "karthik"}
    assert req["result"]["status"] == "success" and req["ledger"]["state"] == "SUCCESS"
    assert demo.sim.runs[-1]["triggered_by"] == "agent"
    audit = client.get("/api/v1/audit?action=rerun_requested", headers=viewer).json()
    assert audit[0]["actor"] == "user:karthik (operator)" and audit[0]["details"]["reason"] == "manual rerun test"


def test_reason_is_mandatory_for_actions(console):
    client, _ = console
    r = client.post("/api/v1/pipelines/rerun", json={"platform_id": "sim", "pipeline_id": "daily_kpis", "reason": ""},
                    headers=login(client, "operator"))
    assert r.status_code == 422


def test_approver_approval_resumes_the_incident(console):
    client, demo = console
    [pending] = client.get("/api/v1/action-approvals", headers=login(client)).json()
    assert pending["capability"] == "pipeline.cancel" and pending["steps"] == ["pipeline.cancel", "pipeline.rerun"]
    url = f"/api/v1/action-approvals/{pending['id']}/approve"
    assert client.post(url, json={"reason": "long run blocks the report"}, headers=login(client, "operator")).status_code == 403
    assert client.post(url, json={"reason": "long run blocks the report"}, headers=login(client, "approver", "lead")).status_code == 200
    assert client.post(url, json={"reason": "again"}, headers=login(client, "approver", "lead")).status_code == 409

    demo.cycle(1)
    demo.cycle(1)
    caps = [q["capability"] for q in client.get("/api/v1/requests", headers=login(client)).json()]
    assert "pipeline.cancel" in caps and "pipeline.rerun" in caps
    assert client.get("/api/v1/action-approvals", headers=login(client)).json() == []


def test_admin_controls_kill_switch_reaches_both_planes(console, store):
    client, demo = console
    assert client.put("/api/v1/controls", json={"enabled": False, "reason": "maintenance"}, headers=login(client, "operator")).status_code == 403
    assert client.put("/api/v1/controls", json={"enabled": False, "reason": "maintenance window"}, headers=login(client, "admin")).status_code == 200
    assert store.get_controls()["enabled"] is False
    assert client.get("/api/v1/overview", headers=login(client)).json()["agent"]["enabled"] is False


def test_takeover_stops_the_graph_and_rejects_its_approvals(console):
    client, demo = console
    h = login(client, "operator", "karthik")
    slow = incident(client, "sales_report")
    assert client.post(f"/api/v1/incidents/{slow['id']}/takeover", json={"reason": "I'll handle this"}, headers=h).status_code == 200
    detail = client.get(f"/api/v1/incidents/{slow['id']}", headers=h).json()
    assert detail["incident"]["status"] == "escalated" and detail["work_items"][0]["status"] == "taken_over"
    assert detail["approvals"][0]["status"] == "rejected"
    demo.cycle(1)
    assert not [q for q in client.get("/api/v1/requests", headers=h).json() if q["capability"] == "pipeline.cancel"]


def test_start_fix_needs_the_fix_path_enabled(console):
    client, _ = console
    orders = incident(client, "orders_pipeline")
    r = client.post(f"/api/v1/incidents/{orders['id']}/fix", json={"reason": "please fix"}, headers=login(client, "approver"))
    assert r.status_code == 409 and "disabled" in r.json()["detail"]


def test_kedb_curation_is_admin_only(console):
    client, demo = console
    entry = demo.deps.kedb.propose("orders_try_cast", r"has no attribute 'try_cast'", "code", "incident:1")
    assert any(e["name"] == "cast_invalid_input" for e in client.get("/api/v1/kedb?status=approved", headers=login(client)).json())
    url = f"/api/v1/kedb/{entry.id}/approve"
    assert client.post(url, json={"reason": "reviewed"}, headers=login(client, "approver")).status_code == 403
    r = client.post(url, json={"reason": "reviewed the fix"}, headers=login(client, "admin", "boss"))
    assert r.status_code == 200 and r.json()["entry"]["status"] == "approved"


def test_agents_and_policy_are_visible(console):
    client, _ = console
    h = login(client)
    names = [a["name"] for a in client.get("/api/v1/agents", headers=h).json()]
    assert names == ["console", "monitoring", "reconciliation", "recovery", "testing", "triage"]
    policy = client.get("/api/v1/policy", headers=h).json()
    assert len(policy["sha256"]) == 64 and any(r["name"] == "human_operator" for r in policy["rules"])


def test_settings_never_expose_secret_values(console):
    client, _ = console
    data = client.get("/api/v1/settings", headers=login(client)).json()
    assert "plugins" in data and data["fix"]["promotion"] == ["dev", "qa", "main"]
    for p in data["platforms"]:
        assert all(v.endswith("://…") for v in p["secrets"].values())


def test_console_reads_platform_state_from_observer_snapshots(console):
    client, demo = console
    assert not hasattr(client.app.state, "instances")
    from pipeline_agent.api.context import ConsoleContext

    assert not hasattr(ConsoleContext, "instances")  # no platform clients, no platform credentials in this process
    h = login(client)
    [platform] = client.get("/api/v1/platforms", headers=h).json()
    assert platform["id"] == "sim" and platform["health"]["ok"] and "cancel" in platform["capabilities"] and platform["last_poll"]
    costs = client.get("/api/v1/costs/compute?days=14", headers=h).json()
    assert costs["is_estimate"] and costs["total"] > 0 and costs["errors"] == []
