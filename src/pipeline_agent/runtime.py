"""Wires settings + config + plugins + store + notifier (+ LLM) into a Monitor."""

from __future__ import annotations

from .config import AgentConfig, Settings, load_agent_config
from .llm.gateway import LLMGateway
from .llm.triage import Triage
from .monitor import Monitor
from .notify.webhook import ConsoleNotifier, Notifier, WebhookNotifier
from .sdk.plugin import PlatformInstance
from .sdk.registry import load_plugin
from .secrets import EnvSecretResolver
from .store.db import StateStore


def build_instances(config: AgentConfig, resolver: EnvSecretResolver | None = None) -> dict[str, PlatformInstance]:
    resolver = resolver or EnvSecretResolver()
    instances: dict[str, PlatformInstance] = {}
    for pc in config.platforms:
        if not pc.enabled:
            continue
        plugin = load_plugin(pc.plugin)
        plugin.validate_config(pc.config)
        instances[pc.id] = plugin.build(pc.id, pc.config, resolver.resolve_all(pc.secrets))
    return instances


def build_notifier(settings: Settings) -> Notifier:
    if settings.notify_kind == "console" or settings.notify_webhook_url is None:
        return ConsoleNotifier()
    return WebhookNotifier(settings.notify_webhook_url.get_secret_value(), settings.notify_kind)


def build_gateway(settings: Settings, config: AgentConfig, store: StateStore) -> LLMGateway:
    return LLMGateway.from_settings(settings, store, config.budgets, config.llm, EnvSecretResolver())


def build_monitor(settings: Settings | None = None) -> Monitor:
    settings = settings or Settings()
    config = load_agent_config(settings.config_dir)
    store = StateStore(settings.database_url)
    triage = Triage(build_gateway(settings, config, store)) if settings.llm_enabled else None
    return Monitor(
        settings=settings,
        config=config,
        instances=build_instances(config),
        store=store,
        notifier=build_notifier(settings),
        triage=triage,
    )
