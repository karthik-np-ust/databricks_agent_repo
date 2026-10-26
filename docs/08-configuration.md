# 08 · Configuration

Configuration lives in two places:
- **Process settings:** environment variables or `.env`, prefix `AGENT_`.
- **Agent config:** YAML files in `config/` (or `AGENT_CONFIG_DIR`). These never contain secret values.

## Settings ([src/pipeline_agent/config.py](../src/pipeline_agent/config.py) `Settings`)
| Variable | Default | Meaning |
|---|---|---|
| `AGENT_ENABLED` | `true` | **Kill switch.** `false` skips every poll. |
| `AGENT_DRY_RUN` | `false` | Record and notify decisions, but never cancel or rerun |
| `AGENT_DATABASE_URL` | `sqlite:///./agent_state.db` | Any SQLAlchemy URL; Postgres: `postgresql+psycopg://user:pw@host/db` |
| `AGENT_CONFIG_DIR` | `config` | Folder with the YAML files |
| `AGENT_POLL_INTERVAL_SECONDS` | `60` | Used by `pipeline-agent run` |
| `AGENT_INITIAL_LOOKBACK_MINUTES` | `60` | First poll ignores runs that ended earlier than this |
| `AGENT_POLL_OVERLAP_MINUTES` | `5` | Each poll re-reads this much history |
| `AGENT_NOTIFY_KIND` | `console` | `console`, `teams`, `slack`, `generic` |
| `AGENT_NOTIFY_WEBHOOK_URL` | unset | Incoming webhook URL (treated as a secret) |
| `AGENT_LLM_ENABLED` | `false` | Turn on LLM triage for new incidents |
| `AGENT_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint (approved provider or LiteLLM proxy later) |
| `AGENT_LLM_API_KEY_REF` | `env://OPENROUTER_API_KEY` | Secret reference for the LLM key |
| `AGENT_LLM_TIMEOUT_SECONDS` | `90` | Per request |
| `AGENT_FIX_ENABLED` | `false` | Phase 3 fix flow (needs `AGENT_LLM_ENABLED=true`) |
| `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` | unset | **Preferred identity:** GitHub App (used when `GITHUB_APP_ID` is set) |
| `AGENT_GITHUB_TOKEN_REF` | `env://GITHUB_TOKEN` | Fallback identity: a bot account's token |
| `AGENT_GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise Server: `https://<host>/api/v3` |

The CLI loads `.env` from the working directory into the environment (real environment variables win), so `env://` references such as `env://DATABRICKS_TOKEN` resolve from `.env` too.

## `config/platforms.yaml`
```yaml
platforms:
  - id: dbx-free              # instance id, appears in run keys and the console
    plugin: databricks        # entry-point name, or "module.path:Class"
    enabled: true
    config: { ... }           # validated against the plugin's config model
    secrets:
      token: env://DATABRICKS_TOKEN
```

### Databricks plugin config
| Key | Default | Meaning |
|---|---|---|
| `host` | required (config or secret) | Workspace URL. Can come from `secrets.host` (e.g. `env://DATABRICKS_HOST`) to keep it out of git. `warehouse_id` works the same way. |
| `env_prefixes` | `{"[dev] ": dev, "[qa] ": qa}` | Job-name prefix → env; anything else is prod |
| `include` | `["*"]` | Globs on job name (prefix removed) |
| `max_run_lookback_hours` | `24` | How far back to look for long runs that finish in the window |
| `max_log_chars` | `20000` | Trace/log tail kept per failed task |
| `use_billing_table` / `warehouse_id` | `false` / unset | Read DBUs from `system.billing.usage` |
| `estimated_dbu_per_hour` / `dbu_rate_usd` | `1.0` / `0.0` | Estimate used when the billing table is off |
| `repo`, `code_root`, `code_paths` | unset, `sample_pipelines`, `{}` | Where the code lives (enables CODE capability) |

### Simulator plugin config
Each entry in `pipelines` has:
- `name`, `env`
- `behaviour`: one of `ok`, `slow`, `fail_code`, `fail_transient`, `fail_permission`, `flaky`
- `duration_seconds`, `slow_duration_seconds`, `fail_attempts`, `flaky_rate`, `error_message`, `dbu_per_hour`, `code_path`

Top-level keys: `state_file` (persists runs between processes), `start_on_boot`, `seed`, `repo`.

## `config/pipelines.yaml`
```yaml
defaults: { ... }          # a PipelinePolicy
policies:                  # first match wins; unset fields fall back to defaults
  - match: "sales_report"  # glob on "<platform_id>:<name>" or "<name>"
    max_duration_seconds: 300
circuit_breaker:
  max_actions_per_hour: 10
```
| Policy field | Default | Meaning |
|---|---|---|
| `max_duration_seconds` | unset | Explicit threshold |
| `baseline_multiplier` / `baseline_min_samples` | `1.5` / `5` | Baseline threshold = p95(recent successes) × multiplier |
| `max_reruns_per_day` | `2` | Agent reruns per pipeline per 24h before escalating |
| `transient_retries` | `1` | Reruns for transient failures |
| `auto_fix` | `false` | Allow the Phase 3 fix flow for code failures |
| `act_in_envs` | `[prod]` | Where the agent may cancel or rerun; elsewhere it only notifies |
| `owner`, `notify_channel` | unset | Routing (used by the console and notifications) |

## `config/budgets.yaml`
Checked before every LLM call, against the `llm_calls` ledger:

| Field | Default | Meaning |
|---|---|---|
| `per_incident_usd` | 0.50 | Cost cap per incident |
| `per_incident_tokens` | 200000 | Token cap per incident |
| `per_day_usd` | 2.00 | Cost cap per UTC day |
| `per_day_requests` | 45 | Request cap per UTC day (OpenRouter free tier is 50) |

Failed attempts count too. When a cap is hit, the incident is escalated and the audit log gets `llm_budget_stop`.

## `config/llm.yaml`
| Field | Meaning |
|---|---|
| `triage_models` | Ordered list; the next one is used on error or rate limit |
| `fix_models` | Phase 3 |
| `max_tokens`, `temperature` | Per call |
| `max_context_chars` | Tail of the (redacted) error text sent to the model |

## `config/fix.yaml` (Phase 3)
| Field | Default (repo file) | Meaning |
|---|---|---|
| `repo` | `karthik-np-ust/databricks_agent_repo` | Repo the agent reads and opens PRs against |
| `base_branch` | `main` | Code the fix starts from (what failed in prod) |
| `promotion` | `[dev, qa, main]` | PR targets in order; the **last one is the human gate** (never merged by the agent) |
| `auto_merge` | `[dev, qa]` | Branches the agent may merge once checks are green (if branch rules allow) |
| `deploy_workflow` | `deploy.yml` | Its run for the merge commit must succeed before promoting |
| `required_checks` | `agent-tests, pipeline-tests, bundle-validate` | Must be green on each PR |
| `allowed_paths` | `sample_pipelines/src/`, `sample_pipelines/tests/` | The only paths a fix may change (CI path-guard enforces the same rule) |
| `context_paths` | bundle YAML files | Read-only context for the model |
| `protected_patterns` | `assert_not_null(` | Count per file may not decrease (no deleting data-quality checks) |
| `forbidden_patterns` | `F.try_cast(`, `.try_cast(` | Platform knowledge: regex + hint. A match is rejected before commit and the hint is fed back to the model |
| `require_test_change` | `true` | A test under `tests/` must be added or changed |
| `max_files`, `max_file_chars`, `max_context_chars` | 6 / 40000 / 60000 | Limits |
| `patch_attempts` | 2 | 1 retry with validation errors fed back |
| `repair_attempts` | 2 | After CI fails on the fix PR: read the CI log, push a corrected commit (then escalate) |
| `max_concurrent` | 1 | Fixes in flight at once |
| `max_wait_hours` | 48 | Waiting longer than this in any state → failed, incident escalated |
