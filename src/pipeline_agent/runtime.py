"""Wires settings + config + plugins + store + notifier (+ LLM) into a Monitor."""

from __future__ import annotations

from pathlib import Path

from .config import AgentConfig, Settings, load_agent_config
from .fix.flow import FixFlow
from .git.github import AppTokenAuth, GitHubClient
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


def build_github(settings: Settings, config: AgentConfig) -> GitHubClient:
    if not config.fix.repo:
        raise ValueError("config/fix.yaml: fix.repo is not set")
    if settings.github_app_id:
        if not (settings.github_app_installation_id and settings.github_app_private_key_path):
            raise ValueError("GitHub App auth needs GITHUB_APP_INSTALLATION_ID and GITHUB_APP_PRIVATE_KEY_PATH")
        key = Path(settings.github_app_private_key_path).read_text(encoding="utf-8")
        auth = AppTokenAuth(settings.github_app_id, settings.github_app_installation_id, key, settings.github_api_url)
        return GitHubClient(config.fix.repo, api_url=settings.github_api_url, auth=auth)
    token = EnvSecretResolver().get(settings.github_token_ref)
    return GitHubClient(config.fix.repo, token, settings.github_api_url)


def runtime_controls(settings: Settings, store: StateStore) -> dict[str, bool]:
    """Console controls override .env; the kill switch can only turn things OFF (.env false always wins)."""
    controls = store.get_controls()
    return {
        "enabled": settings.enabled and controls.get("enabled") is not False,
        "dry_run": controls["dry_run"] if isinstance(controls.get("dry_run"), bool) else settings.dry_run,
    }


def build_policy_engine(settings: Settings):
    from .autonomy import PolicyBundle, PolicyEngine
    from .control_plane.registry import AgentRegistry

    return PolicyEngine(
        PolicyBundle.load(settings.config_dir / "policy" / "autonomy.yaml"), AgentRegistry.load(settings.config_dir / "agents")
    )


def build_data_plane(settings: Settings | None = None):
    """Observer + executor with the platform credentials. Trusts only the configured public keys."""
    from datetime import timedelta

    from .data_plane.executor import Executor
    from .data_plane.observer import Observer
    from .data_plane.router import CapabilityRouter
    from .data_plane.service import DataPlane
    from .evidence import EvidenceStore, LocalBlobs
    from .leases import Leases
    from .security.signing import Verifier

    settings = settings or Settings()
    config = load_agent_config(settings.config_dir)
    store = StateStore(settings.database_url)
    instances = build_instances(config)
    evidence = EvidenceStore(store, LocalBlobs(settings.evidence_dir))
    observer = Observer(
        config, instances, store, evidence,
        initial_lookback=timedelta(minutes=settings.initial_lookback_minutes), overlap=timedelta(minutes=settings.poll_overlap_minutes),
    )
    executor = Executor(
        store, CapabilityRouter(instances), Verifier.from_files(settings.trusted_keys), build_policy_engine(settings), evidence,
        controls=lambda: runtime_controls(settings, store),
    )
    return DataPlane(observer, executor, Leases(store))


def build_monitor(settings: Settings | None = None) -> Monitor:
    settings = settings or Settings()
    config = load_agent_config(settings.config_dir)
    store = StateStore(settings.database_url)
    instances = build_instances(config)
    notifier = build_notifier(settings)
    gateway = build_gateway(settings, config, store) if settings.llm_enabled else None
    triage = Triage(gateway) if gateway else None
    fixer = None
    if settings.fix_enabled:
        if gateway is None:
            raise ValueError("AGENT_FIX_ENABLED needs AGENT_LLM_ENABLED=true")
        fixer = FixFlow(settings, config.fix, store, build_github(settings, config), gateway, config.llm.fix_models, instances, notifier)
    return Monitor(
        settings=settings,
        config=config,
        instances=instances,
        store=store,
        notifier=notifier,
        triage=triage,
        fixer=fixer,
    )
