"""GitHub REST client: just the calls the fix flow needs.

`RepoHost` is the interface the fix flow depends on; tests use an in-memory
fake. Authentication is the agent's own identity, never a person's: GitHub
does not let anyone approve their own PR, so the human gate only works when
the agent is a different identity. Preferred: a GitHub App (`AppTokenAuth`);
fallback: a bot account's token.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

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


_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class AppTokenAuth(httpx.Auth):
    """GitHub App authentication (recommended identity for the agent).

    Signs a 9-minute JWT with the App's private key, exchanges it for a 1-hour
    installation token, and renews that token 5 minutes before it expires.
    Permissions are exactly those granted to the App (no Workflows access means
    GitHub itself rejects any change to .github/workflows).
    """

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key: str,
        api_url: str = "https://api.github.com",
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.app_id = str(app_id)
        self.installation_id = str(installation_id)
        self._key = private_key
        self.api_url = api_url.rstrip("/")
        self._http = http or httpx.Client(timeout=30)
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def __repr__(self) -> str:  # never show the key or token
        return f"AppTokenAuth(app_id={self.app_id!r}, installation_id={self.installation_id!r})"

    def app_jwt(self) -> str:
        import jwt  # PyJWT, only needed for App auth

        now = int(self._clock())
        # iat 60s in the past tolerates clock drift; GitHub caps exp at 10 minutes.
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self._key, algorithm="RS256")

    def _app_call(self, method: str, path: str) -> dict:
        resp = self._http.request(method, f"{self.api_url}{path}", headers={**_HEADERS, "Authorization": f"Bearer {self.app_jwt()}"})
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, f"GitHub App auth failed: {resp.text[:200]}")
        return resp.json()

    def token(self) -> str:
        if self._token is None or self._expires_at - self._clock() < 300:
            data = self._app_call("POST", f"/app/installations/{self.installation_id}/access_tokens")
            self._token = data["token"]
            self._expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
        return self._token

    def app_slug(self) -> str:
        return self._app_call("GET", "/app")["slug"]

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token()}"
        yield request


class GitHubClient:
    def __init__(
        self,
        repo: str,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        http: httpx.Client | None = None,
        auth: AppTokenAuth | None = None,
    ):
        if http is None and token is None and auth is None:
            raise ValueError("GitHubClient needs a token or GitHub App auth")
        self.repo = repo
        self.app_auth = auth
        headers = dict(_HEADERS)
        if token and auth is None:
            headers["Authorization"] = f"Bearer {token}"
        self.http = http or httpx.Client(base_url=api_url, timeout=30, headers=headers, auth=auth)

    def __repr__(self) -> str:  # never show the token
        return f"GitHubClient(repo={self.repo!r})"

    # plumbing -------------------------------------------------------------
    # Reads are retried on network errors and 5xx/429 (proxies and GitHub both
    # hiccup); writes are not retried here, so they can never be duplicated.
    # The fix flow retries a failed step on the next tick, and its writes are idempotent.
    READ_RETRIES = 3

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        attempts = self.READ_RETRIES if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                resp = self.http.request(method, f"/repos/{self.repo}{path}", **kwargs)
            except httpx.TransportError:
                if attempt == attempts:
                    raise
                time.sleep(2 ** attempt)
                continue
            if method == "GET" and (resp.status_code >= 500 or resp.status_code == 429) and attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            break
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
        """Login the agent acts as (`<app-slug>[bot]` for GitHub App auth)."""
        if self.app_auth is not None:
            return f"{self.app_auth.app_slug()}[bot]"
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

    def pr_files(self, number: int) -> list[dict[str, Any]]:
        """Changed files with unified-diff patches (for the console's diff view)."""
        files = self._call("GET", f"/pulls/{number}/files", params={"per_page": 100})
        return [
            {"filename": f["filename"], "status": f["status"], "additions": f["additions"],
             "deletions": f["deletions"], "patch": f.get("patch", "")}
            for f in files
        ]

    def pr_comments(self, number: int) -> list[dict[str, Any]]:
        items = self._call("GET", f"/issues/{number}/comments", params={"per_page": 50})
        return [{"author": c["user"]["login"], "created_at": c["created_at"], "body": c["body"]} for c in items]

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
                # The job log says *why* (annotations usually only say "exit code 1").
                excerpt = self.job_log_excerpt(run["id"])
                notes = [
                    f"{name}: {a.get('message', '')[-1500:]}"
                    for a in self._call("GET", f"/check-runs/{run['id']}/annotations")
                    if a.get("annotation_level") == "failure" and "exit code" not in a.get("message", "")
                ]
                if excerpt:
                    notes.append(f"{name} log:\n{excerpt}")
                failures.extend(notes or [f"{name}: {run['conclusion']}"])
        if failures:
            return ChecksStatus("failure", details, failures)
        done = all(n in latest and latest[n]["status"] == "completed" for n in required)
        return ChecksStatus("success" if done else "pending", details, [])

    _LOG_SIGNAL = re.compile(r"(FAILED|ERROR|Error|error:|Exception|Traceback|^E\s|assert|NameError|AttributeError|SyntaxError| failed| passed)")
    _LOG_TS = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")

    def job_log_excerpt(self, job_id: int, max_lines: int = 60) -> str:
        """Lines from a failed Actions job log that explain the failure (needs Actions: read)."""
        try:
            resp = self.http.get(f"/repos/{self.repo}/actions/jobs/{job_id}/logs", follow_redirects=True)
        except httpx.HTTPError:
            return ""
        if resp.status_code >= 400:
            return ""
        lines = [self._LOG_TS.sub("", line) for line in resp.text.splitlines()]
        # pytest prints the useful part last: failure details, then the short summary.
        keep = [line for line in lines if self._LOG_SIGNAL.search(line) and "##[" not in line]
        return "\n".join(keep[-max_lines:])[-6000:]

    def workflow_run(self, workflow: str, branch: str, sha: str) -> WorkflowRun | None:
        data = self._call(
            "GET", f"/actions/workflows/{workflow}/runs", params={"branch": branch, "head_sha": sha, "per_page": 5}
        )
        runs = data.get("workflow_runs") or []
        if not runs:
            return None
        r = max(runs, key=lambda r: r["id"])
        return WorkflowRun(id=r["id"], status=r["status"], conclusion=r["conclusion"], url=r["html_url"])
