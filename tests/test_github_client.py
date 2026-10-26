"""GitHubClient request shapes, against httpx.MockTransport (no network)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from pipeline_agent.git.github import GitHubClient


def client(handler):
    http = httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(handler))
    return GitHubClient("o/r", "tok", http=http)


def test_commit_files_creates_tree_commit_and_branch():
    calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        if req.url.path.endswith("/git/commits/base"):
            return httpx.Response(200, json={"tree": {"sha": "basetree"}})
        if req.url.path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "newtree"})
        if req.url.path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "newcommit"})
        if req.url.path.endswith("/git/refs"):
            return httpx.Response(422, json={"message": "Reference already exists"})
        if req.method == "PATCH":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": "unexpected"})

    sha = client(handler).commit_files("base", "fix/incident-1", {"a.py": "x=1"}, "msg")
    assert sha == "newcommit"
    tree_call = next(c for c in calls if c[1].endswith("/git/trees"))
    assert tree_call[2]["base_tree"] == "basetree" and tree_call[2]["tree"][0]["path"] == "a.py"
    assert ("PATCH", "/repos/o/r/git/refs/heads/fix/incident-1") in [(m, p) for m, p, _ in calls], "existing branch is moved"


def test_merge_blocked_by_rules_returns_none():
    def handler(req):
        return httpx.Response(405, json={"message": "At least 1 approving review is required"})

    assert client(handler).merge_pr(3, "m") is None


def test_checks_summary_and_failure_annotations():
    runs = {"check_runs": [
        {"id": 1, "name": "agent-tests", "status": "completed", "conclusion": "success"},
        {"id": 2, "name": "pipeline-tests", "status": "completed", "conclusion": "failure"},
        {"id": 3, "name": "path-guard", "status": "completed", "conclusion": "skipped"},
    ]}

    def handler(req):
        if req.url.path.endswith("/check-runs"):
            return httpx.Response(200, json=runs)
        if req.url.path.endswith("/check-runs/2/annotations"):
            return httpx.Response(200, json=[{"annotation_level": "failure", "message": "AssertionError in test_x"}])
        return httpx.Response(200, json=[])

    st = client(handler).checks("sha", ["agent-tests", "pipeline-tests"])
    assert st.state == "failure" and st.failures == ["pipeline-tests: AssertionError in test_x"]
    runs["check_runs"][1].update(status="in_progress", conclusion=None)
    assert client(handler).checks("sha", ["agent-tests", "pipeline-tests"]).state == "pending"


def test_read_file_decodes_and_404_is_none():
    def handler(req):
        if "missing" in req.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"content": base64.b64encode(b"hello").decode()})

    gh = client(handler)
    assert gh.read_file("a.txt", "main") == "hello"
    assert gh.read_file("missing.txt", "main") is None


def test_reads_retry_on_timeout_writes_do_not(monkeypatch):
    import pipeline_agent.git.github as gh_mod

    monkeypatch.setattr(gh_mod.time, "sleep", lambda s: None)
    calls = {"GET": 0, "POST": 0}

    def handler(req):
        calls[req.method] += 1
        if calls[req.method] == 1:
            raise httpx.ReadTimeout("proxy hiccup", request=req)
        return httpx.Response(200, json={"object": {"sha": "abc"}, "sha": "x", "tree": {"sha": "t"}})

    gh = client(handler)
    assert gh.branch_sha("qa") == "abc" and calls["GET"] == 2
    with pytest.raises(httpx.ReadTimeout):
        gh._call("POST", "/git/trees", json={})
    assert calls["POST"] == 1


def test_token_not_in_repr():
    assert "tok" not in repr(GitHubClient("o/r", "secret-token-value"))


def test_github_app_auth_mints_and_refreshes_installation_token():
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from pipeline_agent.git.github import AppTokenAuth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    now = [1_800_000_000.0]
    minted = []

    def handler(req: httpx.Request):
        if req.url.path == "/app/installations/42/access_tokens":
            # Signature must verify; timestamps are checked against the fake clock, not wall time.
            claims = jwt.decode(
                req.headers["Authorization"].split()[1], key.public_key(), algorithms=["RS256"],
                options={"verify_iat": False, "verify_exp": False, "verify_nbf": False},
            )
            assert claims["iat"] == int(now[0]) - 60
            assert claims["iss"] == "7" and claims["exp"] - claims["iat"] <= 600
            minted.append(now[0])
            expires = datetime.fromtimestamp(now[0] + 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            return httpx.Response(201, json={"token": f"ghs_{len(minted)}", "expires_at": expires})
        if req.url.path == "/app":
            return httpx.Response(200, json={"slug": "pipeline-agent-test"})
        assert req.headers["Authorization"] == f"Bearer ghs_{len(minted)}"
        return httpx.Response(200, json={"object": {"sha": "abc"}})

    transport = httpx.MockTransport(handler)
    auth = AppTokenAuth("7", "42", pem, "https://api.github.test", http=httpx.Client(transport=transport), clock=lambda: now[0])
    gh = GitHubClient("o/r", http=httpx.Client(base_url="https://api.github.test", transport=transport, auth=auth), auth=auth)

    assert gh.branch_sha("main") == "abc"
    gh.branch_sha("main")
    assert len(minted) == 1, "token reused while valid"
    now[0] += 3600 - 200  # within 5 minutes of expiry
    gh.branch_sha("main")
    assert len(minted) == 2, "token renewed before it expires"
    assert gh.whoami() == "pipeline-agent-test[bot]"
    assert "BEGIN" not in repr(auth) and "ghs_" not in repr(auth)
