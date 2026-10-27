"""Model gateway: adopt, wrap thinly.

Agents ask for a model *alias* (``triage``, ``fix``, ``test`` from their manifest), never a
model id. The alias resolves to an ordered fallback list from config/llm.yaml; budgets,
redaction and the ``llm_calls`` ledger stay in ``LLMGateway``. Swapping OpenRouter for a
LiteLLM-class proxy, Azure OpenAI or Bedrock changes this mapping, not the agents.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..config import LlmConfig
from .gateway import LLMGateway, LLMResult, LLMUnavailable


class ModelGateway(Protocol):
    def chat(
        self, alias: str, messages: list[dict[str, Any]], *, purpose: str, incident_id: int | None = None,
        tools: list[dict[str, Any]] | None = None, tool_choice: Any = None,
    ) -> LLMResult: ...


def alias_table(config: LlmConfig) -> dict[str, tuple[list[str], int]]:
    """alias -> (models in fallback order, max output tokens)."""
    table = {
        "triage": (config.triage_models, config.max_tokens),
        "fix": (config.fix_models, config.fix_max_tokens),
        # Test notebooks are code too: own list when configured, else the fix models.
        "test": (config.test_models or config.fix_models, config.fix_max_tokens),
    }
    for alias, models in config.aliases.items():
        table[alias] = (models, config.max_tokens)
    return table


class AliasedModelGateway:
    def __init__(self, gateway: LLMGateway, config: LlmConfig):
        self.gateway = gateway
        self.aliases = alias_table(config)

    def models_for(self, alias: str) -> list[str]:
        if alias not in self.aliases:
            raise LLMUnavailable(f"unknown model alias {alias!r}; known: {', '.join(sorted(self.aliases))}")
        return self.aliases[alias][0]

    def chat(
        self, alias: str, messages: list[dict[str, Any]], *, purpose: str, incident_id: int | None = None,
        tools: list[dict[str, Any]] | None = None, tool_choice: Any = None,
    ) -> LLMResult:
        models = self.models_for(alias)
        return self.gateway.chat(
            messages, purpose=purpose, models=models, incident_id=incident_id, tools=tools, tool_choice=tool_choice,
            max_tokens=self.aliases[alias][1],
        )
