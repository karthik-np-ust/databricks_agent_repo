"""Settings (environment / .env) and agent config (config/*.yaml)."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .sdk.models import Env


class Settings(BaseSettings):
    """Process-level settings. Prefix ``AGENT_`` (e.g. ``AGENT_ENABLED=false``)."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    # Kill switch: when false the monitor observes nothing and acts on nothing.
    enabled: bool = True
    # Dry run: decisions are recorded and notified, but no cancel/rerun is sent.
    dry_run: bool = False
    database_url: str = "sqlite:///./agent_state.db"
    config_dir: Path = Path("config")
    poll_interval_seconds: int = 60
    # How far back the first poll looks. Older failures are ignored.
    initial_lookback_minutes: int = 60
    # Each poll re-reads this much history, so slow-to-appear runs aren't missed.
    poll_overlap_minutes: int = 5
    notify_kind: Literal["console", "teams", "slack", "generic"] = "console"
    notify_webhook_url: SecretStr | None = None
    console_base_url: str | None = None
    # LLM triage (Phase 2). Off by default: the agent works without an LLM.
    llm_enabled: bool = False
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key_ref: str = "env://OPENROUTER_API_KEY"
    llm_timeout_seconds: float = 90.0
    # Fix flow (Phase 3). Off by default; needs LLM + a GitHub token for the agent's identity.
    fix_enabled: bool = False
    github_api_url: str = "https://api.github.com"
    # Preferred identity: a GitHub App (no extra account, 1-hour tokens, exact permissions).
    github_app_id: str | None = Field(None, validation_alias=AliasChoices("GITHUB_APP_ID", "AGENT_GITHUB_APP_ID"))
    github_app_installation_id: str | None = Field(
        None, validation_alias=AliasChoices("GITHUB_APP_INSTALLATION_ID", "AGENT_GITHUB_APP_INSTALLATION_ID")
    )
    github_app_private_key_path: Path | None = Field(
        None, validation_alias=AliasChoices("GITHUB_APP_PRIVATE_KEY_PATH", "AGENT_GITHUB_APP_PRIVATE_KEY_PATH")
    )
    # Fallback identity: a bot account's token.
    github_token_ref: str = "env://GITHUB_TOKEN"
    # Ops Console (Phase 4)
    console_auth: Literal["demo", "oidc"] = "demo"
    console_secret: SecretStr | None = None  # signs session tokens; random per start when unset
    console_host: str = "127.0.0.1"
    console_port: int = 8080
    console_dist: Path | None = None  # built frontend; default ./frontend/dist
    # Redesign: trust boundary and shared knowledge
    signing_key_path: Path = Path("var/keys/signing_key.pem")  # control plane only
    trusted_keys: list[Path] = Field(default_factory=lambda: [Path("var/keys/signing_key.pub.pem")])  # data plane
    evidence_dir: Path = Path("var/evidence")
    # Control plane: LangGraph checkpoints (SQLite file; a postgresql:// URL uses PostgresSaver).
    checkpoint_url: str = "var/checkpoints.sqlite"
    approval_timeout_hours: float = 24  # unanswered approval -> escalate
    action_timeout_minutes: float = 120  # no result / run outcome by then -> escalate


class PlatformConfig(BaseModel):
    id: str
    plugin: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class ReconcileCheck(BaseModel):
    """Read-only data check the Reconciliation agent runs after a successful recovery."""

    table: str  # may use {env}, e.g. "{env}.silver.orders"
    keys: list[str] = Field(default_factory=list)  # no duplicates allowed on these
    not_null: list[str] = Field(default_factory=list)
    min_rows: int = 1


class PipelinePolicy(BaseModel):
    # Glob matched against "<platform_id>:<pipeline name>" or the pipeline name alone.
    match: str = "*"
    # Explicit threshold. When unset, the baseline (p95 of recent successful runs
    # x baseline_multiplier) is used once there are enough samples.
    max_duration_seconds: int | None = None
    baseline_multiplier: float = 1.5
    baseline_min_samples: int = 5
    # Agent reruns (threshold or transient) allowed per pipeline per 24h.
    max_reruns_per_day: int = 2
    transient_retries: int = 1
    # Fix flow (Phase 3) may open PRs for code failures of this pipeline.
    auto_fix: bool = False
    # Environments where the agent may act (cancel/rerun). Elsewhere it only notifies.
    act_in_envs: list[Env] = Field(default_factory=lambda: [Env.PROD])
    owner: str | None = None
    notify_channel: str | None = None
    # Redesign: data checks after a successful rerun/repair (Reconciliation agent).
    reconcile: list[ReconcileCheck] = Field(default_factory=list)

    def matches(self, platform_id: str, pipeline_name: str) -> bool:
        return fnmatch.fnmatchcase(f"{platform_id}:{pipeline_name}", self.match) or fnmatch.fnmatchcase(
            pipeline_name, self.match
        )


class CircuitBreaker(BaseModel):
    # Stop all automatic cancel/rerun actions once this many happened in the last hour.
    max_actions_per_hour: int = 10


class Budgets(BaseModel):
    """LLM budgets, enforced by the LLM gateway (Phase 2)."""

    per_incident_usd: float = 0.50
    per_day_usd: float = 2.00
    per_incident_tokens: int = 200_000
    # OpenRouter free tier allows 50 requests/day (1000 after $10 of credits).
    per_day_requests: int = 45


class LlmConfig(BaseModel):
    # Tried in order; the next one is used when a model errors or is rate limited.
    triage_models: list[str] = Field(default_factory=list)
    fix_models: list[str] = Field(default_factory=list)
    # Testing agent (writes Databricks test notebooks); empty = use fix_models.
    test_models: list[str] = Field(default_factory=list)
    # Extra model aliases for agents (alias -> models in fallback order).
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    max_tokens: int = 4000
    fix_max_tokens: int = 12000
    temperature: float = 0.1
    # Characters of (redacted) error text sent to the model.
    max_context_chars: int = 12_000


class ForbiddenPattern(BaseModel):
    pattern: str  # regex, matched per line
    message: str


class FixConfig(BaseModel):
    repo: str | None = None
    base_branch: str = "main"
    promotion: list[str] = Field(default_factory=lambda: ["dev", "qa", "main"])
    auto_merge: list[str] = Field(default_factory=lambda: ["dev", "qa"])
    deploy_workflow: str = "deploy.yml"
    required_checks: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=list)
    protected_patterns: list[str] = Field(default_factory=list)
    # Platform knowledge: code patterns that are known to break at runtime, with the fix hint.
    forbidden_patterns: list["ForbiddenPattern"] = Field(default_factory=list)
    require_test_change: bool = True
    max_files: int = 6
    max_file_chars: int = 40_000
    max_context_chars: int = 60_000
    patch_attempts: int = 2
    repair_attempts: int = 2  # new commits on the fix PR after CI failures, fed with the CI log
    max_concurrent: int = 1
    max_wait_hours: float = 48


class AgentConfig(BaseModel):
    platforms: list[PlatformConfig] = Field(default_factory=list)
    defaults: PipelinePolicy = Field(default_factory=PipelinePolicy)
    policies: list[PipelinePolicy] = Field(default_factory=list)
    circuit_breaker: CircuitBreaker = Field(default_factory=CircuitBreaker)
    budgets: Budgets = Field(default_factory=Budgets)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    fix: FixConfig = Field(default_factory=FixConfig)

    def policy_for(self, platform_id: str, pipeline_name: str) -> PipelinePolicy:
        for policy in self.policies:
            if policy.matches(platform_id, pipeline_name):
                # Unset fields fall back to the defaults.
                merged = self.defaults.model_dump()
                merged.update(policy.model_dump(exclude_unset=True))
                return PipelinePolicy.model_validate(merged)
        return self.defaults


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_agent_config(config_dir: Path) -> AgentConfig:
    platforms = _read_yaml(config_dir / "platforms.yaml")
    pipelines = _read_yaml(config_dir / "pipelines.yaml")
    budgets = _read_yaml(config_dir / "budgets.yaml")
    llm = _read_yaml(config_dir / "llm.yaml")
    fix = _read_yaml(config_dir / "fix.yaml")
    return AgentConfig.model_validate(
        {
            "platforms": platforms.get("platforms", []),
            "defaults": pipelines.get("defaults", {}),
            "policies": pipelines.get("policies", []),
            "circuit_breaker": pipelines.get("circuit_breaker", {}),
            "budgets": budgets.get("budgets", {}),
            "llm": llm.get("llm", {}),
            "fix": fix.get("fix", {}),
        }
    )
