"""LLM gateway: the only path from the agent to a model.

Every call goes through four steps:
  1. budget check  (per-incident tokens/cost, per-day cost/requests; ledger in llm_calls)
  2. redaction     (all message text, again, as defence in depth)
  3. model fallback (configured list, next model on error or rate limit)
  4. ledger write  (tokens, cost, latency, redaction count, success/error)

Works with any OpenAI-compatible endpoint: OpenRouter for the demo, an approved
provider (or a LiteLLM proxy in front of it) later, by changing base_url/key.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from typing import Any, Callable

from ..config import Budgets, LlmConfig
from ..security.redact import redact
from ..store.db import StateStore

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class LLMResult:
    model: str
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def tool_arguments(self, name: str) -> dict[str, Any] | None:
        for call in self.tool_calls:
            if call["name"] == name:
                try:
                    return json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    return None
        return None


def _start_of_day(now: datetime) -> datetime:
    return datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)


class LLMGateway:
    def __init__(
        self,
        client: Any,
        store: StateStore,
        budgets: Budgets,
        config: LlmConfig,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.client = client  # openai.OpenAI-compatible
        self.store = store
        self.budgets = budgets
        self.config = config
        self.clock = clock

    @classmethod
    def from_settings(cls, settings, store: StateStore, budgets: Budgets, config: LlmConfig, resolver) -> "LLMGateway":
        from openai import OpenAI

        client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=resolver.get(settings.llm_api_key_ref),
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
        )
        return cls(client, store, budgets, config)

    # budgets -------------------------------------------------------------
    def check_budget(self, incident_id: int | None) -> None:
        now = self.clock()
        calls, _, day_cost = self.store.llm_usage(since=_start_of_day(now))
        if calls >= self.budgets.per_day_requests:
            raise BudgetExceeded(f"daily request budget reached ({calls}/{self.budgets.per_day_requests})")
        if day_cost >= self.budgets.per_day_usd:
            raise BudgetExceeded(f"daily cost budget reached (${day_cost:.4f}/${self.budgets.per_day_usd})")
        if incident_id is not None:
            _, tokens, cost = self.store.llm_usage(incident_id=incident_id)
            if tokens >= self.budgets.per_incident_tokens:
                raise BudgetExceeded(f"incident token budget reached ({tokens}/{self.budgets.per_incident_tokens})")
            if cost >= self.budgets.per_incident_usd:
                raise BudgetExceeded(f"incident cost budget reached (${cost:.4f}/${self.budgets.per_incident_usd})")

    # calls ---------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        purpose: str,
        models: list[str],
        incident_id: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
    ) -> LLMResult:
        if not models:
            raise LLMUnavailable("no models configured")
        clean, redactions = [], 0
        for m in messages:
            r = redact(m.get("content") or "")
            redactions += r.count
            clean.append({**m, "content": r.text})

        errors = []
        for model in models:
            self.check_budget(incident_id)  # re-checked per attempt: failed attempts count too
            started = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": clean,
                    "max_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                    "extra_body": {"usage": {"include": True}},
                }
                if tools:
                    kwargs["tools"] = tools
                    if tool_choice is not None:
                        kwargs["tool_choice"] = tool_choice
                resp = self.client.chat.completions.create(**kwargs)
                result = self._to_result(resp, model)
            except Exception as exc:
                latency = int((time.monotonic() - started) * 1000)
                self.store.record_llm_call(
                    ts=self.clock(), incident_id=incident_id, purpose=purpose, model=model, latency_ms=latency,
                    redactions=redactions, success=0, error=str(exc)[:500],
                )
                log.warning("LLM call to %s failed: %s", model, exc)
                errors.append(f"{model}: {exc}")
                continue
            self.store.record_llm_call(
                ts=self.clock(), incident_id=incident_id, purpose=purpose, model=result.model,
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd, latency_ms=int((time.monotonic() - started) * 1000), redactions=redactions,
            )
            return result
        raise LLMUnavailable("all models failed: " + " | ".join(errors))

    @staticmethod
    def _to_result(resp: Any, requested_model: str) -> LLMResult:
        choice = resp.choices[0]
        msg = choice.message
        calls = [
            {"name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (getattr(msg, "tool_calls", None) or [])
        ]
        usage = getattr(resp, "usage", None)
        cost = getattr(usage, "cost", None)
        if cost is None and usage is not None and getattr(usage, "model_extra", None):
            cost = usage.model_extra.get("cost")
        return LLMResult(
            model=getattr(resp, "model", None) or requested_model,
            content=msg.content or "",
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=float(cost or 0.0),
        )

    def key_status(self) -> dict[str, Any]:
        """OpenRouter key limits and usage (GET /key). Empty dict for other providers."""
        try:
            raw = self.client.get("/key", cast_to=object)
            return raw.get("data", raw) if isinstance(raw, dict) else {}
        except Exception as exc:
            log.debug("key status unavailable: %s", exc)
            return {}
