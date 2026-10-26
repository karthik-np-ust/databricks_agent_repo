"""GitHub REST client: just the calls the fix flow needs.

`RepoHost` is the interface the fix flow depends on; tests use an in-memory
fake. Authentication is the agent's own identity (a bot account or GitHub App
token), never a person's: GitHub does not let anyone approve their own PR, so
the human gate only works when the agent is a different user.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status


@dataclass
class PullRequest:
    number: int
    url: str
    head_sha: str
    state: str  # open | closed
    merged: bool
    merge_sha: str | None = None


@dataclass
class ChecksStatus:
    state: str  # pending | success | failure
    details: dict[str, str]  # check name -> conclusion/status
    failures: list[str]  # failure annotations (for the retry prompt / PR comment)


@dataclass
class WorkflowRun:
    id: int
    status: str  # queued | in_progress | waiting | completed
    conclusion: str | None
    url: str


class RepoHost(Protocol):
    def branch_sha(self, branch: str) -> str: ...
    def list_files(self, sha: str) -> list[str]: ...
    def read_file(self, path: str, ref: str) -> str | None: ...
    def commit_files(self, base_sha: str, branch: str, files: dict[str, str], message: str) -> str: ...
    def open_pr(self, head: str, base: str, title: str, body: str) -> PullRequest: ...
    def find_open_pr(self, head: str, base: str) -> PullRequest | None: ...
    def get_pr(self, number: int) -> PullRequest: ...
    def merge_pr(self, number: int, message: str) -> str | None: ...
    def comment(self, number: int, body: str) -> None: ...
    def checks(self, sha: str, required: list[str]) -> ChecksStatus: ...
    def workflow_run(self, workflow: str, branch: str, sha: str) -> WorkflowRun | None: ...


class GitHubClient:
    def __init__(self, repo: str, token: str, api_url: str = "https://api.github.com", http: httpx.Client | None = None):
        self.repo = repo
        self.http = http or httpx.Client(
            base_url=api_url,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def __repr__(self) -> str:  # never show the token
        return f"GitHubClient(repo={self.repo!r})"

    # plumbing -------------------------------------------------------------
    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self.http.request(method, f"/repos/{self.repo}{path}", **kwargs)
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise GitHubError(resp.status_code, str(message)[:300])
        return resp.json() if resp.content else None

    @staticmethod
    def _pr(data: dict) -> PullRequest:
        return PullRequest(
            number=data["number"],
            url=data["html_url"],
            head_sha=data["head"]["sha"],
            state=data["state"],
            merged=bool(data.get("merged") or data.get("merged_at")),
            merge_sha=data.get("merge_commit_sha") if (data.get("merged") or data.get("merged_at")) else None,
        )

    def whoami(self) -> str:
        resp = self.http.get("/user")
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, resp.text[:200])
        return resp.json()["login"]

    # contents -------------------------------------------------------------
    def branch_sha(self, branch: str) -> str:
        return self._call("GET", f"/git/ref/heads/{branch}")["object"]["sha"]

    def list_files(self, sha: str) -> list[str]:
        tree = self._call("GET", f"/git/trees/{sha}", params={"recursive": "1"})
        return [e["path"] for e in tree["tree"] if e["type"] == "blob"]

    def read_file(self, path: str, ref: str) -> str | None:
        try:
            data = self._call("GET", f"/contents/{path}", params={"ref": ref})
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        return base64.b64decode(data["content"]).decode("utf-8")

    def commit_files(self, base_sha: str, branch: str, files: dict[str, str], message: str) -> str:
        """One commit with all files on a new branch created from base_sha. Returns the commit sha."""
        base_tree = self._call("GET", f"/git/commits/{base_sha}")["tree"]["sha"]
        entries = [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()]
        tree = self._call("POST", "/git/trees", json={"base_tree": base_tree, "tree": entries})["sha"]
        commit = self._call("POST", "/git/commits", json={"message": message, "tree": tree, "parents": [base_sha]})["sha"]
        try:
            self._call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
        except GitHubError as exc:
            if exc.status != 422:  # 422 = branch exists: move it (retry of the same fix)
                raise
            self._call("PATCH", f"/git/refs/heads/{branch}", json={"sha": commit, "force": True})
        return commit

    # pull requests ----------------------------------------------------------
    def open_pr(self, head: str, base: str, title: str, body: str) -> PullRequest:
        return self._pr(self._call("POST", "/pulls", json={"head": head, "base": base, "title": title, "body": body}))

    def find_open_pr(self, head: str, base: str) -> PullRequest | None:
        owner = self.repo.split("/")[0]
        found = self._call("GET", "/pulls", params={"head": f"{owner}:{head}", "base": base, "state": "open"})
        return self._pr(found[0]) if found else None

    def get_pr(self, number: int) -> PullRequest:
        return self._pr(self._call("GET", f"/pulls/{number}"))

    def merge_pr(self, number: int, message: str) -> str | None:
        """Merge; returns the merge sha, or None when branch rules require a human."""
        try:
            return self._call("PUT", f"/pulls/{number}/merge", json={"commit_title": message, "merge_method": "merge"})["sha"]
        except GitHubError as exc:
            if exc.status in (403, 405, 409):  # review required / not mergeable yet / conflict
                return None
            raise

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"/issues/{number}/comments", json={"body": body})

    # CI ---------------------------------------------------------------------
    def checks(self, sha: str, required: list[str]) -> ChecksStatus:
        runs = self._call("GET", f"/commits/{sha}/check-runs", params={"per_page": 100})["check_runs"]
        latest: dict[str, dict] = {}
        for run in runs:  # keep the newest run per check name
            if run["name"] not in latest or run["id"] > latest[run["name"]]["id"]:
                latest[run["name"]] = run
        details = {n: (r["conclusion"] or r["status"]) for n, r in latest.items()}
        failures = []
        for name in required:
            run = latest.get(name)
            if run and run["status"] == "completed" and run["conclusion"] not in ("success", "skipped", "neutral"):
                notes = [
                    f"{name}: {a.get('message', '')[-1500:]}"
                    for a in self._call("GET", f"/check-runs/{run['id']}/annotations")
                    if a.get("annotation_level") == "failure"
                ]
                failures.extend(notes or [f"{name}: {run['conclusion']}"])
        if failures:
            return ChecksStatus("failure", details, failures)
        done = all(n in latest and latest[n]["status"] == "completed" for n in required)
        return ChecksStatus("success" if done else "pending", details, [])

    def workflow_run(self, workflow: str, branch: str, sha: str) -> WorkflowRun | None:
        data = self._call(
            "GET", f"/actions/workflows/{workflow}/runs", params={"branch": branch, "head_sha": sha, "per_page": 5}
        )
        runs = data.get("workflow_runs") or []
        if not runs:
            return None
        r = max(runs, key=lambda r: r["id"])
        return WorkflowRun(id=r["id"], status=r["status"], conclusion=r["conclusion"], url=r["html_url"])
