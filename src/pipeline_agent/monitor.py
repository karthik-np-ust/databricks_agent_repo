"""Monitor: one poll ("tick") over every platform instance.

Idempotent by design: the store remembers which runs were already handled, so
overlapping polls and restarts never act twice on the same run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from .config import AgentConfig, Settings
from .llm.context import condense_logs
from .llm.gateway import BudgetExceeded
from .llm.triage import Diagnosis, Triage
from .notify.webhook import Notification, Notifier, Severity
from .policy import ActionType, Decision, PolicyEngine
from .sdk.capabilities import Capability
from .sdk.classify import ErrorClassifier
from .sdk.models import FailureContext, Pipeline, Run, RunStatus, utcnow
from .sdk.plugin import PlatformInstance
from .security.audit import AuditLogger
from .security.redact import redact
from .store.db import StateStore

log = logging.getLogger(__name__)


@dataclass
class TickReport:
    started_at: datetime
    skipped: bool = False
    runs_seen: int = 0
    decisions: list[tuple[str, str, str]] = field(default_factory=list)  # (run_key, action, reason)
    errors: list[str] = field(default_factory=list)


class Monitor:
    def __init__(
        self,
        settings: Settings,
        config: AgentConfig,
        instances: dict[str, PlatformInstance],
        store: StateStore,
        notifier: Notifier,
        clock: Callable[[], datetime] = utcnow,
        triage: Triage | None = None,
    ):
        self.triage = triage
        self.settings = settings
        self.config = config
        self.instances = instances
        self.store = store
        self.notifier = notifier
        self.clock = clock
        self.policy = PolicyEngine(config, store)
        self.audit = AuditLogger(store)
        self._classifiers = {
            iid: ErrorClassifier.with_defaults(inst.error_rules) for iid, inst in instances.items()
        }

    # ------------------------------------------------------------------ tick
    def tick(self) -> TickReport:
        now = self.clock()
        report = TickReport(started_at=now)
        if not self.settings.enabled:
            report.skipped = True
            log.warning("Kill switch is on (AGENT_ENABLED=false); skipping poll")
            return report
        for iid, instance in self.instances.items():
            try:
                self._process_instance(instance, now, report)
                self.store.set_watermark(iid, now)
            except Exception as exc:  # one broken platform must not stop the others
                log.exception("Polling %s failed", iid)
                report.errors.append(f"{iid}: {exc}")
                self.audit.log("adapter_error", iid, error=str(exc))
        return report

    def _process_instance(self, instance: PlatformInstance, now: datetime, report: TickReport) -> None:
        watermark = self.store.get_watermark(instance.instance_id)
        if watermark is None:
            since = now - timedelta(minutes=self.settings.initial_lookback_minutes)
        else:
            since = watermark - timedelta(minutes=self.settings.poll_overlap_minutes)

        pipelines = {p.pipeline_id: p for p in instance.run_source.list_pipelines()}
        for run in instance.run_source.list_runs(since=since):
            report.runs_seen += 1
            self.store.upsert_run(run, now)
            decision, ctx = self._evaluate(instance, run, now)
            if decision is None or decision.action == ActionType.NONE:
                continue
            pipeline = pipelines.get(run.pipeline_id) or Pipeline(
                platform_id=run.platform_id, pipeline_id=run.pipeline_id, name=run.pipeline_name, env=run.env
            )
            report.decisions.append((run.key, decision.action.value, decision.reason))
            self._execute(instance, pipeline, run, decision, ctx, now)

    def _already_handled(self, run: Run) -> bool:
        return bool(self.store.incident_for_run(run.key) or self.store.actions_for_run(run.key))

    def _evaluate(
        self, instance: PlatformInstance, run: Run, now: datetime
    ) -> tuple[Decision | None, FailureContext | None]:
        if run.status == RunStatus.RUNNING:
            return self.policy.evaluate_active(run, instance, now), None
        if run.status == RunStatus.FAILED and not self._already_handled(run):
            # Logs are only fetched once per failed run.
            ctx = self._failure_context(instance, run)
            return self.policy.evaluate_failure(run, ctx, instance, now), ctx
        return None, None

    def _failure_context(self, instance: PlatformInstance, run: Run) -> FailureContext:
        if instance.supports(Capability.LOGS):
            ctx = instance.logs.failure_context(run)
        else:
            ctx = FailureContext(run=run, error_message="Run failed (platform provides no logs)")
        # Re-classify with core + platform rules when the plugin left it unknown.
        if ctx.matched_rule is None:
            cls, rule = self._classifiers[instance.instance_id].classify(ctx.full_text())
            ctx.error_class, ctx.matched_rule = cls, rule
        return ctx

    # --------------------------------------------------------------- execute
    def _execute(
        self,
        instance: PlatformInstance,
        pipeline: Pipeline,
        run: Run,
        d: Decision,
        ctx: FailureContext | None,
        now: datetime,
    ) -> None:
        dry = self.settings.dry_run
        facts = {
            "platform": run.platform_id,
            "pipeline": run.pipeline_name,
            "env": run.env.value,
            "run_id": run.run_id,
            "decision": d.action.value,
            "reason": d.reason,
        }
        if dry:
            facts["mode"] = "DRY RUN (no action sent)"

        if d.action in (ActionType.CANCEL_AND_RERUN, ActionType.RERUN):
            new_run_id = None
            if not dry:
                if d.action == ActionType.CANCEL_AND_RERUN:
                    instance.controller.cancel(run)
                ref = instance.controller.rerun(pipeline, run.parameters or None)
                new_run_id = ref.run_id
                facts["new_run_id"] = new_run_id
            self.store.record_action(run, d.action.value, d.reason, d.trigger, new_run_id, dry, now)
            self.audit.log(d.action.value, run.key, reason=d.reason, new_run_id=new_run_id, dry_run=dry)
            title = "Pipeline over threshold: cancelled and rerun" if d.action == ActionType.CANCEL_AND_RERUN else "Transient failure: rerun"
            self.notifier.send(Notification(title, f"{run.pipeline_name} ({run.env})", Severity.WARNING, facts, run.url))
            return

        if d.action == ActionType.NOTIFY:
            self.store.record_action(run, "notify", d.reason, d.trigger, None, dry, now)
            self.audit.log("notify", run.key, reason=d.reason)
            self.notifier.send(Notification("Pipeline issue (notify only)", f"{run.pipeline_name} ({run.env})", Severity.INFO, facts, run.url))
            return

        # OPEN_INCIDENT or ESCALATE
        if d.trigger == "threshold":
            # Long-running run that we will not touch: escalate once.
            self.store.record_action(run, "escalate", d.reason, d.trigger, None, dry, now)
            self.audit.log("escalate", run.key, reason=d.reason)
            self.notifier.send(Notification("Pipeline over threshold: needs a human", run.pipeline_name, Severity.CRITICAL, facts, run.url))
            return

        message = condense_logs(redact(ctx.full_text() if ctx else "").text, 4000)
        status = "open" if d.action == ActionType.OPEN_INCIDENT else "escalated"
        incident = self.store.create_incident(
            run,
            status=status,
            error_class=str(d.error_class or "unknown"),
            error_message=message,
            matched_rule=ctx.matched_rule if ctx else None,
            fix_eligible=d.fix_eligible,
            now=now,
        )
        self.audit.log(
            "incident_opened" if status == "open" else "incident_escalated",
            run.key,
            incident_id=incident.id,
            error_class=incident.error_class,
            reason=d.reason,
        )
        facts.update({"incident": str(incident.id), "error_class": incident.error_class})
        first_line = next((line for line in message.splitlines() if line.strip()), "no error text")
        self.notifier.send(
            Notification(
                f"Pipeline failed: {run.pipeline_name}",
                first_line[:300],
                Severity.CRITICAL if status == "escalated" or run.env.value == "prod" else Severity.WARNING,
                facts,
                run.url,
            )
        )
        if self.triage is not None:
            full_text = redact(ctx.full_text() if ctx else "").text
            self.run_triage(incident.id, full_text, now)

    # ---------------------------------------------------------------- triage
    def run_triage(self, incident_id: int, error_text: str, now: datetime | None = None) -> Diagnosis | None:
        """LLM diagnosis for an incident. Never raises: LLM problems must not stop monitoring."""
        incident = self.store.get_incident(incident_id)
        target = incident.run_key
        try:
            diag = self.triage.diagnose(
                incident_id=incident.id,
                platform=incident.platform_id,
                pipeline=incident.pipeline_name,
                env=incident.env,
                error_text=error_text,
                rule_class=incident.error_class,
                matched_rule=incident.matched_rule,
            )
        except BudgetExceeded as exc:
            # Scenario 14: stop spending, hand over to a human.
            self.store.update_incident(incident.id, now=now, status="escalated")
            self.audit.log("llm_budget_stop", target, incident_id=incident.id, reason=str(exc))
            self.notifier.send(Notification(
                f"LLM budget reached: incident #{incident.id} needs a human",
                str(exc), Severity.CRITICAL, {"pipeline": incident.pipeline_name, "incident": str(incident.id)}, incident.url,
            ))
            return None
        except Exception as exc:
            self.audit.log("llm_unavailable", target, incident_id=incident.id, error=str(exc)[:300])
            return None

        _, tokens, cost = self.store.llm_usage(incident_id=incident.id)
        self.store.update_incident(
            incident.id, now=now, diagnosis=diag.model_dump(mode="json"),
            suspicious_content=int(diag.suspicious_content), llm_tokens=tokens, llm_cost_usd=cost,
        )
        self.audit.log(
            "diagnosed", target, incident_id=incident.id, model=diag.model, llm_class=diag.error_class.value,
            confidence=diag.confidence, fix_type=diag.fix_type, tokens=tokens, cost_usd=cost,
        )
        if diag.suspicious_content:
            self.audit.log("suspicious_content", target, incident_id=incident.id)
        facts = {
            "incident": str(incident.id),
            "llm class": f"{diag.error_class.value} ({diag.confidence:.0%})",
            "component": diag.failing_component or "-",
            "suggested fix": diag.suggested_fix[:300],
            "tokens / cost": f"{tokens} / ${cost:.4f}",
        }
        if diag.suspicious_content:
            facts["security"] = "SUSPICIOUS CONTENT in logs/data (possible prompt injection) - review carefully"
        self.notifier.send(Notification(
            f"Diagnosis: {incident.pipeline_name}", diag.summary,
            Severity.CRITICAL if diag.suspicious_content else Severity.INFO, facts, incident.url,
        ))
        return diag
