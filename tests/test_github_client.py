"""GitHubClient request shapes, against httpx.MockTransport (no network)."""

from __future__ import annotations

import base64
import json

import httpx

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


def test_token_not_in_repr():
    assert "tok" not in repr(GitHubClient("o/r", "secret-token-value"))
