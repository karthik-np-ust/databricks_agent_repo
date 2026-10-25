"""State store (SQLAlchemy). SQLite for local development, Postgres for every hosted stage.

Tables:
  runs         last known state of every observed run
  actions      everything the agent did (cancel+rerun, rerun, escalate, notify)
  incidents    failures that need a human or the fix flow
  audit_log    append-only record of agent and human actions
  watermarks   last successful poll per platform instance
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ..sdk.models import Run, RunStatus, utcnow


class Base(DeclarativeBase):
    pass


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; everything in the store is UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class RunRow(Base):
    __tablename__ = "runs"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    platform_id: Mapped[str] = mapped_column(String(100), index=True)
    pipeline_id: Mapped[str] = mapped_column(String(200), index=True)
    pipeline_name: Mapped[str] = mapped_column(String(300))
    run_id: Mapped[str] = mapped_column(String(200))
    env: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by: Mapped[str | None] = mapped_column(String(50))
    url: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionRow(Base):
    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    platform_id: Mapped[str] = mapped_column(String(100))
    pipeline_id: Mapped[str] = mapped_column(String(200), index=True)
    run_key: Mapped[str] = mapped_column(String(300), index=True)
    action: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(String(40))  # threshold | transient | failure | manual
    result_run_id: Mapped[str | None] = mapped_column(String(200))
    dry_run: Mapped[int] = mapped_column(Integer, default=0)


class IncidentRow(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    platform_id: Mapped[str] = mapped_column(String(100))
    pipeline_id: Mapped[str] = mapped_column(String(200), index=True)
    pipeline_name: Mapped[str] = mapped_column(String(300))
    run_key: Mapped[str] = mapped_column(String(300), index=True)
    env: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))  # open | escalated | resolved
    error_class: Mapped[str] = mapped_column(String(20))
    matched_rule: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str] = mapped_column(Text)  # always redacted
    fix_eligible: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str | None] = mapped_column(Text)
    # Phase 2: LLM triage result (redacted JSON) and its cost
    diagnosis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    suspicious_content: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class LlmCallRow(Base):
    """Ledger of every LLM call: the source of truth for token and cost budgets."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    incident_id: Mapped[int | None] = mapped_column(Integer, index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class WatermarkRow(Base):
    __tablename__ = "watermarks"
    platform_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_poll: Mapped[datetime] = mapped_column(DateTime(timezone=True))


RERUN_ACTIONS = ("cancel_and_rerun", "rerun")


class StateStore:
    def __init__(self, database_url: str):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self._session()

    # runs -----------------------------------------------------------------
    def get_run(self, key: str) -> RunRow | None:
        with self.session() as s:
            row = s.get(RunRow, key)
            if row:
                row.start_time, row.end_time = _aware(row.start_time), _aware(row.end_time)
            return row

    def upsert_run(self, run: Run, now: datetime | None = None) -> RunRow | None:
        """Store the run and return the previous state (None when first seen)."""
        now = now or utcnow()
        with self.session() as s:
            row = s.get(RunRow, run.key)
            previous = None
            if row is not None:
                previous = RunRow(**{c: getattr(row, c) for c in RunRow.__table__.columns.keys()})
            else:
                row = RunRow(key=run.key)
                s.add(row)
            row.platform_id, row.pipeline_id, row.pipeline_name = run.platform_id, run.pipeline_id, run.pipeline_name
            row.run_id, row.env, row.status = run.run_id, run.env.value, run.status.value
            row.start_time, row.end_time = run.start_time, run.end_time
            row.triggered_by, row.url, row.last_seen = run.triggered_by, run.url, now
            s.commit()
            return previous

    def successful_durations(self, platform_id: str, pipeline_id: str, limit: int = 30) -> list[float]:
        with self.session() as s:
            rows = s.scalars(
                select(RunRow)
                .where(
                    RunRow.platform_id == platform_id,
                    RunRow.pipeline_id == pipeline_id,
                    RunRow.status == RunStatus.SUCCEEDED.value,
                    RunRow.start_time.is_not(None),
                    RunRow.end_time.is_not(None),
                )
                .order_by(RunRow.end_time.desc())
                .limit(limit)
            ).all()
            return [(_aware(r.end_time) - _aware(r.start_time)).total_seconds() for r in rows]

    # actions --------------------------------------------------------------
    def record_action(
        self,
        run: Run,
        action: str,
        reason: str,
        trigger: str,
        result_run_id: str | None = None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> None:
        with self.session() as s:
            s.add(
                ActionRow(
                    created_at=now or utcnow(),
                    platform_id=run.platform_id,
                    pipeline_id=run.pipeline_id,
                    run_key=run.key,
                    action=action,
                    reason=reason,
                    trigger=trigger,
                    result_run_id=result_run_id,
                    dry_run=int(dry_run),
                )
            )
            s.commit()

    def actions_for_run(self, run_key: str) -> list[ActionRow]:
        with self.session() as s:
            return list(s.scalars(select(ActionRow).where(ActionRow.run_key == run_key)).all())

    def count_reruns(
        self, platform_id: str, pipeline_id: str, since: datetime, trigger: str | None = None
    ) -> int:
        with self.session() as s:
            q = select(func.count(ActionRow.id)).where(
                ActionRow.platform_id == platform_id,
                ActionRow.pipeline_id == pipeline_id,
                ActionRow.action.in_(RERUN_ACTIONS),
                ActionRow.created_at >= since,
            )
            if trigger:
                q = q.where(ActionRow.trigger == trigger)
            return s.scalar(q) or 0

    def count_actions_since(self, since: datetime) -> int:
        with self.session() as s:
            q = select(func.count(ActionRow.id)).where(
                ActionRow.action.in_(RERUN_ACTIONS), ActionRow.created_at >= since, ActionRow.dry_run == 0
            )
            return s.scalar(q) or 0

    # incidents ------------------------------------------------------------
    def incident_for_run(self, run_key: str) -> IncidentRow | None:
        with self.session() as s:
            return s.scalars(select(IncidentRow).where(IncidentRow.run_key == run_key)).first()

    def create_incident(
        self,
        run: Run,
        status: str,
        error_class: str,
        error_message: str,
        matched_rule: str | None,
        fix_eligible: bool,
        now: datetime | None = None,
    ) -> IncidentRow:
        now = now or utcnow()
        with self.session() as s:
            row = IncidentRow(
                created_at=now,
                updated_at=now,
                platform_id=run.platform_id,
                pipeline_id=run.pipeline_id,
                pipeline_name=run.pipeline_name,
                run_key=run.key,
                env=run.env.value,
                status=status,
                error_class=error_class,
                matched_rule=matched_rule,
                error_message=error_message,
                fix_eligible=int(fix_eligible),
                url=run.url,
            )
            s.add(row)
            s.commit()
            return row

    def get_incident(self, incident_id: int) -> IncidentRow | None:
        with self.session() as s:
            return s.get(IncidentRow, incident_id)

    def update_incident(self, incident_id: int, now: datetime | None = None, **fields: Any) -> IncidentRow:
        with self.session() as s:
            row = s.get(IncidentRow, incident_id)
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = now or utcnow()
            s.commit()
            return row

    # llm ledger -----------------------------------------------------------
    def record_llm_call(self, **fields: Any) -> None:
        with self.session() as s:
            s.add(LlmCallRow(ts=fields.pop("ts", None) or utcnow(), **fields))
            s.commit()

    def llm_usage(self, since: datetime | None = None, incident_id: int | None = None) -> tuple[int, int, float]:
        """(calls, tokens, cost_usd) for the filter."""
        with self.session() as s:
            q = select(
                func.count(LlmCallRow.id),
                func.coalesce(func.sum(LlmCallRow.prompt_tokens + LlmCallRow.completion_tokens), 0),
                func.coalesce(func.sum(LlmCallRow.cost_usd), 0.0),
            )
            if since is not None:
                q = q.where(LlmCallRow.ts >= since)
            if incident_id is not None:
                q = q.where(LlmCallRow.incident_id == incident_id)
            calls, tokens, cost = s.execute(q).one()
            return int(calls), int(tokens), float(cost)

    def list_llm_calls(self, limit: int = 50) -> list[LlmCallRow]:
        with self.session() as s:
            return list(s.scalars(select(LlmCallRow).order_by(LlmCallRow.id.desc()).limit(limit)).all())

    def list_incidents(self, status: str | None = None) -> list[IncidentRow]:
        with self.session() as s:
            q = select(IncidentRow).order_by(IncidentRow.id.desc())
            if status:
                q = q.where(IncidentRow.status == status)
            return list(s.scalars(q).all())

    # audit ----------------------------------------------------------------
    def append_audit(self, actor: str, action: str, target: str, details: dict[str, Any], now: datetime | None = None) -> None:
        # Round-trip through JSON so only serialisable values are stored.
        safe = json.loads(json.dumps(details, default=str))
        with self.session() as s:
            s.add(AuditRow(ts=now or utcnow(), actor=actor, action=action, target=target, details=safe))
            s.commit()

    def list_audit(self, limit: int = 100) -> list[AuditRow]:
        with self.session() as s:
            return list(s.scalars(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)).all())

    # watermarks -----------------------------------------------------------
    def get_watermark(self, platform_id: str) -> datetime | None:
        with self.session() as s:
            row = s.get(WatermarkRow, platform_id)
            return _aware(row.last_poll) if row else None

    def set_watermark(self, platform_id: str, ts: datetime) -> None:
        with self.session() as s:
            row = s.get(WatermarkRow, platform_id) or WatermarkRow(platform_id=platform_id)
            row.last_poll = ts
            s.merge(row)
            s.commit()


def since_hours(hours: float, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(hours=hours)
