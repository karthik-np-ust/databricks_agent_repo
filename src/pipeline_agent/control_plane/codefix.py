"""Code-fix path of the incident graph (Recovery fix writer + Testing agent + promotion).

    fix_write -> fix_tests -> [tests.run via decide/submit/wait] --fail--> fix_repair -> [tests.run] ...
                                         |pass
                                     fix_next -> fix_open_pr -> fix_wait_checks -> fix_merge -> fix_deploy   (dev, qa: L4)
                                              -> fix_open_pr -> fix_wait_merge (human merge in GitHub)    -> fix_deploy   (prod: L3)
    fix_deploy = [code.sync env, smoke run (dev/qa) | repair of the failed prod run] via decide/submit/wait
    after prod: reconciliation checks -> reconcile -> close (+ KEDB proposal)

GitHub work (commits, PRs, merges) is control-plane work: the repo is the customer's code
host, not the data platform. Every platform change still goes through a signed request.
"""

from __future__ import annotations

import re
from typing import Any

from langgraph.types import interrupt
from sqlalchemy import select

from ..autonomy import PolicyInput
from ..boundary.capabilities import CapabilityId
from ..fix.patch import CodeContext, FixWriter
from ..llm.gateway import BudgetExceeded
from ..notify.webhook import Severity
from ..store.db import RunRow
from .agents.planner import reconcile_steps
from .agents.testing import TestWriter
from .state import IncidentState, Step

TEXT_FILES = (".py", ".csv", ".yml", ".yaml", ".sql", ".txt", ".json", ".md")


class CodeFixNodes:
    """Mixin for IncidentGraph: expects self.d (ControlPlaneDeps), self._audit, self._notify."""

    # helpers -------------------------------------------------------------------
    @property
    def _cfg(self):
        return self.d.config.fix

    def _env_of(self, branch: str) -> str:
        return "prod" if branch == self._cfg.base_branch else branch

    def _ctx(self, sha: str) -> CodeContext:
        cfg = self._cfg
        files = {}
        for path in self.d.repo.list_files(sha):
            if not path.endswith(TEXT_FILES):
                continue
            if any(path.startswith(p) for p in cfg.allowed_paths) or path in cfg.context_paths:
                content = self.d.repo.read_file(path, sha)
                if content is not None:
                    files[path] = content
        readonly = [p for p in files if not any(p.startswith(prefix) for prefix in cfg.allowed_paths)]
        return CodeContext(files=files, allowed_paths=cfg.allowed_paths, readonly_paths=readonly)

    def _writer(self) -> FixWriter:
        # Pipeline code only: tests come from the Testing agent.
        return FixWriter(self.d.models.gateway, self._cfg.model_copy(update={"require_test_change": False}))

    def _fix_update(self, s: IncidentState, note: str, **fields: Any) -> None:
        fix = s.get("fix") or {}
        if fix.get("fix_id"):
            self.d.store.update_fix(fix["fix_id"], now=self.d.clock(), note=note, **fields)

    def _escalate_fix(self, s: IncidentState, reason: str, **fix: Any) -> dict[str, Any]:
        return {"outcome": "escalated", "outcome_reason": reason, "fix": {**(s.get("fix") or {}), **fix}}

    def _pipeline_in(self, s: IncidentState, env: str) -> str | None:
        """The env's job id for this pipeline, from runs the observer has seen."""
        with self.d.store.session() as session:
            return session.scalar(
                select(RunRow.pipeline_id).where(
                    RunRow.platform_id == s["platform_id"], RunRow.pipeline_name == s["pipeline_name"], RunRow.env == env,
                ).order_by(RunRow.last_seen.desc()).limit(1)
            )

    # nodes -----------------------------------------------------------------------
    def fix_write(self, s: IncidentState) -> dict[str, Any]:
        incident = self.d.store.get_incident(s["incident_id"])
        base_sha = self.d.repo.branch_sha(self._cfg.base_branch)
        try:
            proposal, errors = self._writer().propose(incident, incident.error_message, self._ctx(base_sha), self.d.models.models_for("fix"))
        except BudgetExceeded as exc:
            return self._escalate_fix(s, f"LLM budget reached while writing the fix: {exc}")
        if proposal is None:
            return self._escalate_fix(s, "no valid fix: " + "; ".join(errors)[:600])
        branch = f"fix/incident-{incident.id}"
        files = {f.path: f.content for f in proposal.files}
        head = self.d.repo.commit_files(
            base_sha, branch, files,
            f"fix({incident.pipeline_name}): {proposal.summary[:72]}\n\nAutomated fix for incident #{incident.id} by pipeline-agent (Recovery agent).",
        )
        fix_row = self.d.store.fix_for_incident(incident.id) or self.d.store.create_fix(incident.id, "planned", now=self.d.clock())
        record = {"summary": proposal.summary, "files": sorted(files), "risk": proposal.risk, "test_plan": proposal.test_plan, "model": proposal.model}
        self.d.store.update_fix(fix_row.id, now=self.d.clock(), branch=branch, commit_sha=head, proposal=record, note="fix written; testing in Databricks")
        self.d.store.update_incident(incident.id, now=self.d.clock(), status="fixing")
        self._audit(s, "fix_written", branch=branch, files=sorted(files), model=proposal.model, risk=proposal.risk)
        return {"fix": {
            "fix_id": fix_row.id, "branch": branch, "base_sha": base_sha, "head": head, "summary": proposal.summary,
            "risk": proposal.risk, "source_files": sorted(files), "test_files": [], "repairs": 0, "stage": 0, "phase": "tests",
        }}

    def fix_tests(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        incident = self.d.store.get_incident(s["incident_id"])
        ctx = self._ctx(fix["head"])
        notebooks = [p for p in fix["source_files"] if p.startswith("notebooks/")] or [p for p in ctx.files if p.startswith("notebooks/")]
        try:
            proposal, errors = TestWriter(self.d.models, attempts=self._cfg.patch_attempts).propose(
                incident, incident.error_message, fix["summary"], ctx, notebooks
            )
        except BudgetExceeded as exc:
            return self._escalate_fix(s, f"LLM budget reached while writing tests: {exc}")
        if proposal is None:
            return self._escalate_fix(s, "Testing agent produced no valid test notebook: " + "; ".join(errors)[:600])
        files = {f.path: f.content for f in proposal.files}
        fix["head"] = self.d.repo.commit_files(fix["head"], fix["branch"], files, f"test: {proposal.summary[:72]}\n\nWritten by the pipeline-agent Testing agent.")
        fix["test_files"], fix["tests_summary"] = sorted(files), proposal.summary
        self._fix_update(s, f"tests written: {', '.join(sorted(files))}", commit_sha=fix["head"])
        self._audit(s, "tests_written", files=sorted(files), model=proposal.model, summary=proposal.summary)
        return {"fix": fix, "steps": [self._tests_step(s, fix)], "step": 0}

    def _tests_step(self, s: IncidentState, fix: dict[str, Any]) -> Step:
        return Step(
            capability=CapabilityId.TESTS_RUN.value, agent="testing", env="dev", watch=True,
            target={"pipeline_id": s["pipeline_id"]},
            params={"branch": fix["branch"], "notebooks": [p.removesuffix(".py") for p in fix["test_files"]], "notebook_params": {"catalog": "dev"}},
        )

    def fix_repair(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        attempt = fix["repairs"] + 1
        incident = self.d.store.get_incident(s["incident_id"])
        branch_ctx, baseline = self._ctx(fix["head"]), self._ctx(fix["base_sha"])
        carry = {p: c for p, c in branch_ctx.files.items() if baseline.files.get(p) != c and p not in baseline.readonly_paths}
        try:
            proposal, errors = self._writer().propose(
                incident, incident.error_message, branch_ctx, self.d.models.models_for("fix"),
                ci_failures=[fix.get("failure") or "tests failed"], baseline=baseline, carry=carry,
            )
        except BudgetExceeded as exc:
            return self._escalate_fix(s, f"LLM budget reached while repairing the fix: {exc}")
        if proposal is None:
            return self._escalate_fix(s, f"repair attempt {attempt} produced no valid change: " + "; ".join(errors)[:600])
        files = {f.path: f.content for f in proposal.files}
        fix["head"] = self.d.repo.commit_files(fix["head"], fix["branch"], files, f"fix: repair attempt {attempt} after {fix.get('failure_source', 'test')} failure\n\n{proposal.summary[:500]}")
        fix.update(repairs=attempt, phase="tests", summary=f"{fix['summary']} | repair {attempt}: {proposal.summary}")
        fix["source_files"] = sorted({*fix["source_files"], *[p for p in files if not p.startswith("tests/")]})
        self._fix_update(s, f"repair attempt {attempt}: {proposal.summary[:200]}", commit_sha=fix["head"])
        self._audit(s, "fix_repaired", attempt=attempt, files=sorted(files), model=proposal.model, source=fix.get("failure_source"))
        # Earlier failed runs no longer count. Tests run again; an open fix PR then gets its checks re-awaited.
        results = [{**r, "superseded": True} for r in s.get("results", [])]
        return {"fix": fix, "results": results, "steps": [self._tests_step(s, fix)], "step": 0}

    def fix_next(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        promotion = self._cfg.promotion
        if fix["phase"] == "tests":
            tests = next((r for r in reversed(s.get("results", [])) if r["capability"] == CapabilityId.TESTS_RUN.value), {})
            self._audit(s, "tests_passed", run=(tests.get("run") or {}).get("url"), files=fix["test_files"])
            fix["phase"] = "pr"
            return {"fix": fix, "steps": [], "step": 0}
        if fix["phase"] == "deploy":
            target = promotion[fix["stage"]]
            self._audit(s, "fix_deployed", branch=target, env=self._env_of(target))
            self._fix_update(s, f"deployed to {self._env_of(target)}", state="deploying")
            if fix["stage"] == len(promotion) - 1:
                fix["phase"] = "done"
                steps = reconcile_steps(s, self.d)
                return {"fix": fix, "steps": steps, "step": 0}
            fix.update(stage=fix["stage"] + 1, phase="pr", pr=None)
            return {"fix": fix, "steps": [], "step": 0}
        return {}

    def fix_open_pr(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        promotion = self._cfg.promotion
        target = promotion[fix["stage"]]
        head = fix["branch"] if fix["stage"] == 0 else promotion[fix["stage"] - 1]
        incident = self.d.store.get_incident(s["incident_id"])
        title = f"[agent] Fix incident #{incident.id}: {incident.pipeline_name}" if fix["stage"] == 0 else f"[agent] Promote fix for incident #{incident.id} to {target}"
        pr = self.d.repo.find_open_pr(head, target) or self.d.repo.open_pr(head, target, title, self._pr_body(s, incident, fix, head, target))
        fix["pr"] = {"number": pr.number, "url": pr.url}
        fix["prs"] = {**fix.get("prs", {}), target: pr.url}
        self._fix_update(s, f"PR #{pr.number} {head} -> {target}", state="pr_open", stage=fix["stage"], pr_number=pr.number, prs=fix["prs"])
        self._audit(s, "fix_pr_opened", pr=pr.url, branch=target)
        if fix["stage"] == len(promotion) - 1:
            self._notify(s, f"Approval needed: fix for incident #{incident.id} is ready for {target}",
                         "Tests passed in Databricks and the fix works in dev and qa. Review and merge the PR to deploy to prod.",
                         Severity.WARNING, pr=pr.url)
            self._audit(s, "fix_awaiting_approval", pr=pr.url)
        return {"fix": fix}

    def fix_wait_checks(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        checks = interrupt({"wait": "checks", "pr": fix["pr"]["number"]})
        if checks.get("state") == "success":
            return {"fix": {**fix, "phase": "merge"}}
        failures = checks.get("failures") or [checks.get("error") or "checks failed"]
        if checks.get("state") == "failure" and fix["stage"] == 0 and fix["repairs"] < self._cfg.repair_attempts:
            return {"fix": {**fix, "phase": "repair", "failure": "\n".join(failures), "failure_source": "CI"}}
        return self._escalate_fix(s, f"checks on PR {fix['pr']['url']} {checks.get('state')}: " + "; ".join(failures)[:400])

    def fix_merge(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        target = self._cfg.promotion[fix["stage"]]
        env = self._env_of(target)
        controls = self.d.controls()
        decision = self.d.engine.evaluate(PolicyInput(
            agent="recovery", capability=CapabilityId.CODE_MERGE, env=env, pattern=s["error_class"], rule=s.get("matched_rule"),
            enabled=bool(controls.get("enabled", True)), dry_run=bool(controls.get("dry_run", False)),
        ))
        self._audit(s, "policy_decision", capability="code.merge", env=env, level=decision.level, mode=decision.mode, reasons=decision.reasons)
        if not decision.allow:
            return self._escalate_fix(s, "code.merge: " + "; ".join(decision.reasons))
        if decision.mode == "dry_run":
            return {"outcome": "recommended", "outcome_reason": f"dry run: would merge {fix['pr']['url']} into {target}", "fix": fix}
        sha = target in self._cfg.auto_merge and self.d.repo.merge_pr(fix["pr"]["number"], f"Merge agent fix for incident #{s['incident_id']} into {target}")
        if not sha:
            self._audit(s, "fix_merge_blocked", pr=fix["pr"]["url"], reason="branch rules require a human")
            return {"fix": {**fix, "phase": "human_merge"}}
        self._fix_update(s, f"agent merged PR #{fix['pr']['number']} into {target}", merge_sha=sha)
        self._audit(s, "fix_pr_merged", pr=fix["pr"]["url"], branch=target, by="agent")
        return {"fix": {**fix, "merge_sha": sha, "phase": "merged"}}

    def fix_wait_merge(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        merged = interrupt({"wait": "pr_merged", "pr": fix["pr"]["number"]})
        if merged.get("status") != "merged":
            why = "closed without merging" if merged.get("status") == "closed" else merged.get("error", "not merged in time")
            return self._escalate_fix(s, f"PR {fix['pr']['url']} {why}")
        target = self._cfg.promotion[fix["stage"]]
        self._fix_update(s, f"PR #{fix['pr']['number']} merged into {target}", merge_sha=merged.get("merge_sha"))
        self._audit(s, "fix_pr_merged", pr=fix["pr"]["url"], branch=target, by="human")
        approval = {"kind": "github_merge", "ref": fix["pr"]["url"], "by": merged.get("by")}
        return {"fix": {**fix, "merge_sha": merged.get("merge_sha"), "phase": "merged"}, "approval": approval}

    def fix_deploy(self, s: IncidentState) -> dict[str, Any]:
        fix = dict(s["fix"])
        target = self._cfg.promotion[fix["stage"]]
        env = self._env_of(target)
        sync = Step(capability=CapabilityId.CODE_SYNC.value, agent="recovery", env=env, watch=False,
                    target={"pipeline_id": s["pipeline_id"], "env": env}, params={"expected_head": fix.get("merge_sha") or ""})
        if env == "prod":
            prove = Step(capability=CapabilityId.REPAIR.value, agent="recovery", env="prod", watch=True,
                         target={"pipeline_id": s["pipeline_id"], "run_id": s["run_id"]}, params={})
            steps = [sync, prove]
        else:
            job = self._pipeline_in(s, env)
            smoke = [Step(capability=CapabilityId.SMOKE_RUN.value, agent="recovery", env=env, watch=True, target={"pipeline_id": job}, params={})] if job else []
            if not job:
                self._audit(s, "smoke_run_skipped", env=env, reason=f"no {env} job seen for {s['pipeline_name']}")
            steps = [sync, *smoke]
        return {"fix": {**fix, "phase": "deploy"}, "steps": steps, "step": 0}

    # projection -------------------------------------------------------------------
    def fix_closed(self, s: IncidentState, outcome: str, reason: str) -> None:
        fix = s.get("fix") or {}
        if not fix.get("fix_id"):
            return
        state = "resolved" if outcome == "resolved" else "failed"
        self.d.store.update_fix(fix["fix_id"], now=self.d.clock(), state=state, error=None if state == "resolved" else reason, note=reason)
        self.d.audit.log(f"fix_{state}", f"fix:{fix['fix_id']}", actor="agent", incident_id=s.get("incident_id"), reason=reason)
        if state == "resolved":
            self._propose_known_error(s, fix)

    def _propose_known_error(self, s: IncidentState, fix: dict[str, Any]) -> None:
        kedb = s.get("kedb") or {}
        headline = (s.get("hints") or {}).get("error_headline") or ""
        signature = kedb.get("signature") or re.escape(headline[:80]) or re.escape(s["pipeline_name"])
        entry = self.d.kedb.propose(
            f"{s.get('matched_rule') or 'code'}:incident-{s['incident_id']}", signature, "code", f"incident:{s['incident_id']}",
            platform=kedb.get("platform", "*"), runbook="code_fix", diagnostics=(s.get("diagnosis") or {}).get("root_cause", ""),
            fix_hint=fix.get("summary", "")[:1000], risk=fix.get("risk", "medium"),
            rollback="revert: " + ", ".join(fix.get("prs", {}).values()),
        )
        self._audit(s, "kedb_proposed", entry=entry.name, id=entry.id)

    def _pr_body(self, s: IncidentState, incident, fix: dict[str, Any], head: str, target: str) -> str:
        diag = incident.diagnosis or {}
        tests = next((r for r in reversed(s.get("results", [])) if r["capability"] == CapabilityId.TESTS_RUN.value and not r.get("superseded")), {})
        run_url = (tests.get("run") or {}).get("url") or "-"
        warning = "\n> [!WARNING]\n> The failure output contained suspicious content (possible prompt injection). Review with extra care.\n" if incident.suspicious_content else ""
        return (
            f"## Incident #{incident.id}: {incident.pipeline_name} ({incident.env})\n{warning}\n"
            f"**Failure:** `{incident.error_class}` ({incident.matched_rule or 'no rule'})\n\n"
            f"**Diagnosis:** {diag.get('summary', '-')}\n\n"
            f"**Fix (Recovery agent):** {fix['summary']}\n\n**Risk:** {fix.get('risk', '-')}\n\n"
            f"**Tests (Testing agent):** {fix.get('tests_summary', '-')}\n"
            f"Files: {', '.join(fix['test_files'])}. Passed in Databricks on `{fix['branch']}`: {run_url}\n\n"
            f"**Promotion:** {head} -> {target}. dev/qa merge automatically after checks; prod needs your merge.\n\n"
            "_Opened by pipeline-agent. Every platform action is a signed, policy-checked request in the execution ledger._"
        )

