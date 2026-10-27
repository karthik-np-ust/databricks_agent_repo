"""Agent registry: plug-in agents described by manifests (config/agents/*.yaml).

A manifest is the source of truth for what an agent may *request*: the policy engine denies
any capability that isn't listed. ``card()`` renders an A2A-style agent card for discovery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from ..boundary.capabilities import CapabilityId


class Skill(BaseModel):
    id: str
    description: str


class AgentManifest(BaseModel):
    name: str
    version: str
    description: str
    kind: Literal["deterministic", "llm"]
    phase: int = 1
    model_alias: str | None = None
    capabilities: list[CapabilityId] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    risk_class: Literal["low", "medium", "high"] = "low"
    skills: list[Skill] = Field(default_factory=list)

    @model_validator(mode="after")
    def _llm_needs_alias(self) -> "AgentManifest":
        if self.kind == "llm" and not self.model_alias:
            raise ValueError("llm agents need a model_alias")
        return self


class AgentRegistry:
    def __init__(self, manifests: list[AgentManifest]):
        self._agents = {m.name: m for m in manifests}

    @classmethod
    def load(cls, directory: Path) -> "AgentRegistry":
        manifests = [
            AgentManifest.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(Path(directory).glob("*.yaml"))
        ]
        return cls(manifests)

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def get(self, name: str) -> AgentManifest:
        return self._agents[name]

    def names(self) -> list[str]:
        return sorted(self._agents)

    def allows(self, agent: str, capability: CapabilityId) -> bool:
        m = self._agents.get(agent)
        return m is not None and capability in m.capabilities

    def card(self, name: str) -> dict[str, Any]:
        m = self.get(name)
        return {
            "name": m.name,
            "description": m.description,
            "version": m.version,
            "capabilities": {"streaming": False, "pushNotifications": False},
            "skills": [s.model_dump() for s in m.skills],
            "x-pipeline-agent": {
                "kind": m.kind,
                "model_alias": m.model_alias,
                "requests": [c.value for c in m.capabilities],
                "tools": m.tools,
                "risk_class": m.risk_class,
            },
        }
