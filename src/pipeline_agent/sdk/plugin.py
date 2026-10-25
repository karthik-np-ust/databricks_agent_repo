"""Plugin contract: manifest, platform instance and the plugin base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar, Protocol

from pydantic import BaseModel, Field

from .capabilities import (
    Capability,
    CodeLocator,
    CostProvider,
    EventSource,
    LogProvider,
    RunController,
    RunSource,
)
from .classify import ErrorRule


class CapabilityNotSupported(RuntimeError):
    pass


class PluginManifest(BaseModel):
    name: str
    version: str
    plugin_api_version: str
    display_name: str
    description: str = ""
    capabilities: set[Capability]
    # JSON Schema of the plugin's config. The console's onboarding wizard
    # renders its form from this, so a new tool needs no frontend code.
    config_schema: dict[str, Any] = Field(default_factory=dict)
    # Names of the secrets the plugin needs (values are secret references).
    secrets: list[str] = Field(default_factory=list)
    icon: str | None = None


class SecretResolver(Protocol):
    def get(self, ref: str) -> str: ...


class PlatformInstance:
    """One configured connection, e.g. "dbx-free" or "snowflake-trial".

    Capabilities are whatever implementations were supplied, limited to what
    the manifest declares.
    """

    def __init__(
        self,
        instance_id: str,
        manifest: PluginManifest,
        *,
        run_source: RunSource,
        controller: RunController | None = None,
        logs: LogProvider | None = None,
        costs: CostProvider | None = None,
        code: CodeLocator | None = None,
        events: EventSource | None = None,
        error_rules: list[ErrorRule] | None = None,
        healthcheck: Callable[[], str] | None = None,
    ):
        self.instance_id = instance_id
        self.manifest = manifest
        self.run_source = run_source
        self.controller = controller
        self.logs = logs
        self.costs = costs
        self.code = code
        self.events = events
        self.error_rules = error_rules or []
        self._healthcheck = healthcheck

    @property
    def capabilities(self) -> set[Capability]:
        provided = {Capability.MONITOR}
        if self.controller is not None:
            provided |= {Capability.CANCEL, Capability.RERUN}
        if self.logs is not None:
            provided.add(Capability.LOGS)
        if self.costs is not None:
            provided.add(Capability.COST)
        if self.code is not None:
            provided.add(Capability.CODE)
        if self.events is not None:
            provided.add(Capability.EVENTS)
        return provided & self.manifest.capabilities

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def require(self, cap: Capability) -> None:
        if not self.supports(cap):
            raise CapabilityNotSupported(f"{self.instance_id} ({self.manifest.name}) does not support {cap}")

    def healthcheck(self) -> str:
        if self._healthcheck is None:
            self.run_source.list_pipelines()
            return "ok"
        return self._healthcheck()

    def __repr__(self) -> str:  # never include config or secrets
        return f"PlatformInstance(id={self.instance_id!r}, plugin={self.manifest.name!r})"


class PlatformPlugin(ABC):
    manifest: ClassVar[PluginManifest]
    config_model: ClassVar[type[BaseModel]]

    def validate_config(self, config: dict[str, Any]) -> BaseModel:
        return self.config_model.model_validate(config)

    @abstractmethod
    def build(self, instance_id: str, config: dict[str, Any], secrets: dict[str, str]) -> PlatformInstance:
        """Create a platform instance. ``secrets`` holds resolved secret values."""
