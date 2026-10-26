"""Fix flow: incident -> patch -> PRs through dev, qa -> human-approved main -> prod rerun.

A small state machine persisted in `fix_attempts`. Every monitor tick calls
`advance_all()`, which moves each attempt at most one step, so the flow
survives restarts and can wait hours for CI, deploys or a human.

    PLANNED --patch+commit+PR--> PR_OPEN(stage) --merged--> DEPLOYING(stage)
        DEPLOYING(stage) --deploy ok--> PR_OPEN(stage+1) ... last stage = human gate
        DEPLOYING(last) --deploy ok--> rerun prod --> VERIFYING --> RESOLVED
    any step: CI/deploy failure -> FAILED, PR closed unmerged -> REJECTED,
              waiting longer than max_wait_hours -> FAILED   (incident escalated)

The agent merges only into `auto_merge` branches and only when CI is green.
The final PR into `main` is always merged by a human (and branch rules
enforce it); the prod deploy is additionally gated by the `prod` Environment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Callable

from ..config import FixConfig, Settings
from ..git.github import RepoHost
from ..llm.gateway import BudgetExceeded, LLMGateway
from ..notify.webhook import Notification, Notifier, Severity
from ..sdk.models import Pipeline, RunStatus, utcnow
from ..sdk.plugin import PlatformInstance
from ..security.audit import AuditLogger
from ..store.db import FixAttemptRow, StateStore
from .patch import CodeContext, FixProposal, FixWriter

log = logging.getLogger(__name__)


class FixState(StrEnum):
    PLANNED = "planned"
    PR_OPEN = "pr_open"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    FAILED = "failed"
    REJECTED = "rejected"


ACTIVE = (FixState.PLANNED, FixState.PR_OPEN, FixState.DEPLOYING, FixState.VERIFYING)
TERMINAL = (FixState.RESOLVED, FixState.FAILED, FixState.REJECTED)


class FixFlow:
    def __init__(
        self,
        settings: Settings,
        cfg: FixConfig,
        store: StateStore,
        repo: RepoHost,
        gateway: LLMGateway,
        fix_models: list[str],
        instances: dict[str, PlatformInstance],
        notifier: Notifier,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.settings = settings
        self.cfg = cfg
        self.store = store
        self.repo = repo
        self.writer = FixWriter(gateway, cfg)
        self.fix_models = fix_models
        self.instances = instances
        self.notifier = notifier
        self.clock = clock
        self.audit = AuditLogger(store)

    # ------------------------------------------------------------------ start
    def start(self, incident_id: int) -> FixAttemptRow | None:
        existing = self.store.fix_for_incident(incident_id)
        if existing and existing.state in ACTIVE:
            return existing
        active = self.store.list_fixes(tuple(ACTIVE))
        if len(active) >= self.cfg.max_concurrent:
            self.audit.log("fix_deferred", f"incident:{incident_id}", reason=f"{len(active)} fix(es) already in flight")
            return None
        fix = self.store.create_fix(incident_id, FixState.PLANNED, now=self.clock())
        self.store.update_incident(incident_id, now=self.clock(), status="fixing")
        self.audit.log("fix_started", f"incident:{incident_id}", fix_id=fix.id)
        return fix

    # ---------------------------------------------------------------- advance
    def advance_all(self) -> list[tuple[int, str, str]]:
        """Advance every active attempt one step. Returns (fix_id, old_state, new_state)."""
        moves = []
        for fix in self.store.list_fixes(tuple(ACTIVE)):
            before = fix.state
            try:
                after = self.advance(fix)
            except Exception as exc:  # GitHub/Databricks hiccup: retry next tick
                log.exception("fix %s: step failed", fix.id)
                self.audit.log("fix_step_error", f"fix:{fix.id}", state=fix.state, error=str(exc)[:300])
                after = fix.state
            moves.append((fix.id, before, after))
        return moves

    def advance(self, fix: FixAttemptRow) -> str:
        now = self.clock()
        if fix.state != FixState.PLANNED and now - fix.updated_at > timedelta(hours=self.cfg.max_wait_hours):
            return self._fail(fix, f"no progress for {self.cfg.max_wait_hours}h in state {fix.state}")
        step = {
            FixState.PLANNED: self._plan,
            FixState.PR_OPEN: self._pr_open,
            FixState.DEPLOYING: self._deploying,
            FixState.VERIFYING: self._verifying,
        }[FixState(fix.state)]
        return step(fix)

    # ------------------------------------------------------------------ steps
    def _plan(self, fix: FixAttemptRow) -> str:
        incident = self.store.get_incident(fix.incident_id)
        base_sha = self.repo.branch_sha(self.cfg.base_branch)
        ctx = self._code_context(base_sha)
        try:
            proposal, errors = self.writer.propose(incident, incident.error_message, ctx, self.fix_models)
        except BudgetExceeded as exc:
            return self._fail(fix, f"LLM budget reached while writing the fix: {exc}")
        if proposal is None:
            return self._fail(fix, "no valid fix: " + "; ".join(errors)[:800])

        branch = f"fix/incident-{incident.id}"
        files = {f.path: f.content for f in proposal.files}
        message = f"fix({incident.pipeline_name}): {proposal.summary[:72]}\n\nAutomated fix for incident #{incident.id} by pipeline-agent."
        commit = self.repo.commit_files(base_sha, branch, files, message)
        target = self.cfg.promotion[0]
        # Reuse an open PR if a previous attempt of this step got that far (idempotent retries).
        pr = self.repo.find_open_pr(branch, target) or self.repo.open_pr(
            branch, target, f"[agent] Fix incident #{incident.id}: {incident.pipeline_name}", self._pr_body(incident, proposal, target)
        )
        record = {"summary": proposal.summary, "files": sorted(files), "risk": proposal.risk, "test_plan": proposal.test_plan, "model": proposal.model}
        self.store.update_fix(
            fix.id, now=self.clock(), state=FixState.PR_OPEN, branch=branch, commit_sha=commit, stage=0,
            pr_number=pr.number, prs={target: pr.url}, proposal=record, note=f"PR #{pr.number} -> {target}",
        )
        self.audit.log("fix_pr_opened", f"fix:{fix.id}", pr=pr.url, branch=target, files=sorted(files), model=proposal.model)
        self._notify(f"Fix PR opened for incident #{incident.id}", f"{proposal.summary} (-> {target})", Severity.INFO, incident, pr.url)
        return FixState.PR_OPEN

    def _pr_open(self, fix: FixAttemptRow) -> str:
        target = self.cfg.promotion[fix.stage]
        last = fix.stage == len(self.cfg.promotion) - 1
        pr = self.repo.get_pr(fix.pr_number)
        if pr.merged:
            self.store.update_fix(fix.id, now=self.clock(), state=FixState.DEPLOYING, merge_sha=pr.merge_sha, note=f"PR #{pr.number} merged into {target}")
            self.audit.log("fix_pr_merged", f"fix:{fix.id}", pr=pr.url, branch=target)
            return FixState.DEPLOYING
        if pr.state == "closed":
            return self._finish(fix, FixState.REJECTED, f"PR #{pr.number} into {target} was closed without merging")

        checks = self.repo.checks(pr.head_sha, self.cfg.required_checks)
        if checks.state == "failure":
            repairs = sum(1 for h in fix.history or [] if (h.get("note") or "").startswith("repair attempt"))
            # Only the agent's own fix PR is repaired; a failing promotion PR means something else broke.
            if fix.stage == 0 and repairs < self.cfg.repair_attempts:
                return self._repair(fix, pr, checks.failures, repairs + 1)
            self.repo.comment(pr.number, "pipeline-agent: required checks failed, handing over to a human.\n\n```\n" + "\n".join(checks.failures)[:3000] + "\n```")
            return self._fail(fix, f"CI failed on PR #{pr.number}: " + "; ".join(checks.failures)[:500])
        if checks.state != "success":
            return fix.state
        if last:
            if not any(h.get("note") == "awaiting approval" for h in fix.history or []):
                self.store.update_fix(fix.id, now=self.clock(), note="awaiting approval")
                incident = self.store.get_incident(fix.incident_id)
                self._notify(
                    f"Approval needed: fix for incident #{incident.id} is ready for {target}",
                    "CI is green in dev and qa. Review and merge the PR to deploy to prod.",
                    Severity.WARNING, incident, pr.url,
                )
                self.audit.log("fix_awaiting_approval", f"fix:{fix.id}", pr=pr.url)
            return fix.state
        if target in self.cfg.auto_merge:
            sha = self.repo.merge_pr(pr.number, f"Merge agent fix for incident #{fix.incident_id} into {target}")
            if sha:
                self.store.update_fix(fix.id, now=self.clock(), state=FixState.DEPLOYING, merge_sha=sha, note=f"agent merged PR #{pr.number} into {target}")
                self.audit.log("fix_pr_merged", f"fix:{fix.id}", pr=pr.url, branch=target, by="agent")
                return FixState.DEPLOYING
            if not any(h.get("note") == "merge blocked" for h in fix.history or []):
                self.store.update_fix(fix.id, now=self.clock(), note="merge blocked")
                self.audit.log("fix_merge_blocked", f"fix:{fix.id}", pr=pr.url, reason="branch rules require a human")
        return fix.state

    def _repair(self, fix: FixAttemptRow, pr, failures: list[str], attempt: int) -> str:
        """CI failed on the fix PR: feed the CI output back to the model and push a corrected commit."""
        incident = self.store.get_incident(fix.incident_id)
        head = self.repo.branch_sha(fix.branch)
        branch_ctx = self._code_context(head)
        baseline = self._code_context(self.repo.branch_sha(self.cfg.base_branch))
        carry = {p: c for p, c in branch_ctx.files.items() if baseline.files.get(p) != c and p not in baseline.readonly_paths}
        try:
            proposal, errors = self.writer.propose(
                incident, incident.error_message, branch_ctx, self.fix_models,
                ci_failures=failures, baseline=baseline, carry=carry,
            )
        except BudgetExceeded as exc:
            return self._fail(fix, f"LLM budget reached while repairing the fix: {exc}")
        if proposal is None:
            self.repo.comment(pr.number, "pipeline-agent: CI failed and no valid repair was found, handing over to a human.\n\n- " + "\n- ".join(errors)[:2000])
            return self._fail(fix, f"repair attempt {attempt} produced no valid change: " + "; ".join(errors)[:600])
        files = {f.path: f.content for f in proposal.files}
        commit = self.repo.commit_files(head, fix.branch, files, f"fix: repair attempt {attempt} after CI failure\n\n{proposal.summary[:500]}")
        excerpt = "\n".join(failures)[:2500]
        self.repo.comment(
            pr.number,
            f"pipeline-agent: CI failed, so I pushed **repair attempt {attempt}/{self.cfg.repair_attempts}**.\n\n"
            f"**What I changed:** {proposal.summary}\n\n<details><summary>CI output I fixed</summary>\n\n```\n{excerpt}\n```\n</details>",
        )
        record = {**(fix.proposal or {}), "repairs": attempt, "last_repair": proposal.summary, "files": sorted({*(fix.proposal or {}).get("files", []), *files})}
        self.store.update_fix(fix.id, now=self.clock(), commit_sha=commit, proposal=record, note=f"repair attempt {attempt}: {proposal.summary[:200]}")
        self.audit.log("fix_repaired", f"fix:{fix.id}", attempt=attempt, pr=pr.url, files=sorted(files), model=proposal.model)
        return fix.state

    def _deploying(self, fix: FixAttemptRow) -> str:
        target = self.cfg.promotion[fix.stage]
        run = self.repo.workflow_run(self.cfg.deploy_workflow, target, fix.merge_sha)
        if run is None or run.status != "completed":
            return fix.state
        if run.conclusion != "success":
            return self._fail(fix, f"deploy to {target} {run.conclusion}: {run.url}")
        self.audit.log("fix_deployed", f"fix:{fix.id}", branch=target, run=run.url)
        if fix.stage == len(self.cfg.promotion) - 1:
            return self._rerun_prod(fix)
        nxt = self.cfg.promotion[fix.stage + 1]
        incident = self.store.get_incident(fix.incident_id)
        pr = self.repo.find_open_pr(target, nxt) or self.repo.open_pr(
            target, nxt, f"[agent] Promote fix for incident #{incident.id} to {nxt}",
            self._promotion_body(fix, incident, target, nxt),
        )
        prs = {**(fix.prs or {}), nxt: pr.url}
        self.store.update_fix(fix.id, now=self.clock(), state=FixState.PR_OPEN, stage=fix.stage + 1, pr_number=pr.number, prs=prs, note=f"PR #{pr.number} {target} -> {nxt}")
        self.audit.log("fix_pr_opened", f"fix:{fix.id}", pr=pr.url, branch=nxt)
        return FixState.PR_OPEN

    def _rerun_prod(self, fix: FixAttemptRow) -> str:
        incident = self.store.get_incident(fix.incident_id)
        instance = self.instances.get(incident.platform_id)
        if self.settings.dry_run or instance is None or instance.controller is None:
            why = "dry run" if self.settings.dry_run else "platform cannot rerun"
            return self._finish(fix, FixState.RESOLVED, f"deployed to prod; rerun not sent ({why}), verify manually")
        pipeline = next((p for p in instance.run_source.list_pipelines() if p.pipeline_id == incident.pipeline_id), None) or Pipeline(
            platform_id=incident.platform_id, pipeline_id=incident.pipeline_id, name=incident.pipeline_name
        )
        ref = instance.controller.rerun(pipeline)
        self.store.update_fix(fix.id, now=self.clock(), state=FixState.VERIFYING, rerun_run_id=ref.run_id, note=f"prod rerun {ref.run_id}")
        self.audit.log("fix_prod_rerun", f"fix:{fix.id}", run_id=ref.run_id)
        return FixState.VERIFYING

    def _verifying(self, fix: FixAttemptRow) -> str:
        incident = self.store.get_incident(fix.incident_id)
        instance = self.instances[incident.platform_id]
        since = fix.updated_at - timedelta(minutes=10)
        run = next((r for r in instance.run_source.list_runs(since) if r.run_id == fix.rerun_run_id), None)
        if run is None or not run.status.is_terminal:
            return fix.state
        if run.status == RunStatus.SUCCEEDED:
            return self._finish(fix, FixState.RESOLVED, f"prod rerun {run.run_id} succeeded")
        return self._fail(fix, f"prod rerun {run.run_id} ended {run.status}")

    # ---------------------------------------------------------------- helpers
    def _fail(self, fix: FixAttemptRow, reason: str) -> str:
        return self._finish(fix, FixState.FAILED, reason)

    def _finish(self, fix: FixAttemptRow, state: FixState, reason: str) -> str:
        self.store.update_fix(fix.id, now=self.clock(), state=state, error=None if state == FixState.RESOLVED else reason, note=reason)
        incident = self.store.get_incident(fix.incident_id)
        self.store.update_incident(incident.id, now=self.clock(), status="resolved" if state == FixState.RESOLVED else "escalated")
        self.audit.log(f"fix_{state.value}", f"fix:{fix.id}", incident_id=incident.id, reason=reason)
        severity = Severity.INFO if state == FixState.RESOLVED else Severity.CRITICAL
        title = {FixState.RESOLVED: "Incident resolved by agent fix", FixState.REJECTED: "Agent fix rejected", FixState.FAILED: "Agent fix failed: needs a human"}[state]
        url = (fix.prs or {}).get(self.cfg.promotion[min(fix.stage, len(self.cfg.promotion) - 1)])
        self._notify(f"{title} (incident #{incident.id})", reason, severity, incident, url)
        return state

    def _notify(self, title: str, summary: str, severity: Severity, incident, url: str | None) -> None:
        facts = {"pipeline": incident.pipeline_name, "env": incident.env, "incident": str(incident.id)}
        self.notifier.send(Notification(title, summary, severity, facts, url or incident.url))

    def _code_context(self, sha: str) -> CodeContext:
        wanted = [
            p for p in self.repo.list_files(sha)
            if any(p.startswith(prefix) for prefix in self.cfg.allowed_paths) or p in self.cfg.context_paths
        ]
        files = {}
        for path in wanted:
            if not path.endswith((".py", ".csv", ".yml", ".yaml", ".sql", ".txt")):
                continue
            content = self.repo.read_file(path, sha)
            if content is not None:
                files[path] = content
        readonly = [p for p in files if not any(p.startswith(prefix) for prefix in self.cfg.allowed_paths)]
        return CodeContext(files=files, allowed_paths=self.cfg.allowed_paths, readonly_paths=readonly)

    def _pr_body(self, incident, proposal: FixProposal, target: str) -> str:
        diag = incident.diagnosis or {}
        first_error = next((line for line in incident.error_message.splitlines() if line.strip()), "")
        _, tokens, cost = self.store.llm_usage(incident_id=incident.id)
        warning = (
            "\n> [!WARNING]\n> The failure output contained **suspicious content** (possible prompt injection). "
            "Review this change with extra care.\n" if incident.suspicious_content else ""
        )
        files = "\n".join(f"- `{f.path}`" for f in proposal.files)
        return f"""## Automated fix for incident #{incident.id}
{warning}
| | |
|---|---|
| Pipeline | `{incident.pipeline_name}` ({incident.platform_id}, {incident.env}) |
| Failed run | {incident.url or incident.run_key} |
| Error class | `{incident.error_class}` (rule `{incident.matched_rule}`) |
| Fix risk (model's estimate) | {proposal.risk} |
| Model | `{proposal.model}` |
| LLM usage so far | {tokens} tokens, ${cost:.4f} |

**Error**
```
{first_error[:600]}
```

**Diagnosis:** {diag.get('summary', '-')}

**Root cause:** {diag.get('root_cause', '-')}

**What this change does:** {proposal.summary}

**Test plan:** {proposal.test_plan}

**Files changed**
{files}

**Promotion:** this PR targets `{target}`. After CI and deploy succeed, the agent promotes it through {' -> '.join(self.cfg.promotion)}.
The PR into `{self.cfg.promotion[-1]}` needs a human review; prod is never changed without it.

<sub>Opened by pipeline-agent. Guards passed: allowed paths only, Python compiles, data-quality checks kept, test included, no secrets.</sub>
"""

    def _promotion_body(self, fix: FixAttemptRow, incident, source: str, target: str) -> str:
        p = fix.proposal or {}
        gate = "**Human approval required.** Merging this deploys the fix to prod." if target == self.cfg.promotion[-1] else "The agent merges this once CI is green."
        links = "\n".join(f"- `{b}`: {u}" for b, u in (fix.prs or {}).items())
        return f"""## Promote fix for incident #{incident.id} ({incident.pipeline_name}): `{source}` -> `{target}`

{gate}

**Fix:** {p.get('summary', '-')}
**Files:** {', '.join(f'`{f}`' for f in p.get('files', []))}
**Risk (model's estimate):** {p.get('risk', '-')} | **Model:** `{p.get('model', '-')}`

Deployed and smoke-tested in `{source}` (deploy workflow green). Earlier PRs:
{links}
"""
