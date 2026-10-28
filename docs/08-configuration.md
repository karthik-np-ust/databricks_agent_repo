# 08 · Configuration

Configuration lives in two places:
- **Process settings:** environment variables or `.env`, prefix `AGENT_`.
- **Agent config:** YAML files in `config/` (or `AGENT_CONFIG_DIR`). These never contain secret values.

## Settings ([src/pipeline_agent/config.py](../src/pipeline_agent/config.py) `Settings`)
| Variable | Default | Meaning |
|---|---|---|
| `AGENT_ENABLED` | `true` | **Kill switch.** `false` skips every poll. |
| `AGENT_DRY_RUN` | `false` | Decide, sign and check everything; the data plane executes nothing (result `dry_run`) |
| `AGENT_DATABASE_URL` | `sqlite:///./agent_state.db` | Any SQLAlchemy URL; Postgres: `postgresql+psycopg://user:pw@host/db` |
| `AGENT_CONFIG_DIR` | `config` | Folder with the YAML files |
| `AGENT_POLL_INTERVAL_SECONDS` | `60` | Tick interval of `dataplane` and `control` |
| `AGENT_INITIAL_LOOKBACK_MINUTES` | `60` | First poll ignores runs that ended earlier than this |
| `AGENT_POLL_OVERLAP_MINUTES` | `5` | Each poll re-reads this much history |
| `AGENT_NOTIFY_KIND` | `console` | `console`, `teams`, `slack`, `generic` |
| `AGENT_NOTIFY_WEBHOOK_URL` | unset | Incoming webhook URL (treated as a secret) |
| `AGENT_LLM_ENABLED` | `false` | Turn on LLM triage for new incidents |
| `AGENT_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint (approved provider or LiteLLM proxy later) |
| `AGENT_LLM_API_KEY_REF` | `env://OPENROUTER_API_KEY` | Secret reference for the LLM key |
| `AGENT_LLM_TIMEOUT_SECONDS` | `90` | Per request |
| `AGENT_FIX_ENABLED` | `false` | Code-fix path: Recovery + Testing agents (needs `AGENT_LLM_ENABLED=true` and the GitHub identity) |
| `AGENT_SIGNING_KEY_PATH` | `var/keys/signing_key.pem` | **Control plane only.** Private Ed25519 key that signs ActionRequests (`pipeline-agent keys generate`) |
| `AGENT_TRUSTED_KEYS` | `["var/keys/signing_key.pub.pem"]` | **Data plane only.** Public keys whose requests it executes (several = key rotation) |
| `AGENT_EVIDENCE_DIR` | `var/evidence` | Evidence blobs (data plane writes, others read) |
| `AGENT_CHECKPOINT_URL` | `var/checkpoints.sqlite` | LangGraph checkpoints; a `postgresql://` URL uses PostgresSaver |
| `AGENT_APPROVAL_TIMEOUT_HOURS` | `24` | Unanswered L3 approval → escalate |
| `AGENT_ACTION_TIMEOUT_MINUTES` | `120` | No action result / run outcome / PR checks by then → escalate |
| `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` | unset | **Preferred identity:** GitHub App (used when `GITHUB_APP_ID` is set) |
| `AGENT_GITHUB_TOKEN_REF` | `env://GITHUB_TOKEN` | Fallback identity: a bot account's token |
| `AGENT_GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise Server: `https://<host>/api/v3` |

The CLI loads `.env` from the working directory into the environment (real environment variables win), so `env://` references such as `env://DATABRICKS_TOKEN` resolve from `.env` too.

## Which process needs what

| | dataplane | control | console |
|---|---|---|---|
| `DATABRICKS_HOST` / `_TOKEN` / `_WAREHOUSE_ID` | ✅ | – | – |
| `AGENT_TRUSTED_KEYS` (public key) | ✅ | – | – |
| `AGENT_SIGNING_KEY_PATH` (private key) | – | ✅ | – |
| `OPENROUTER_API_KEY` (+ `AGENT_LLM_ENABLED`) | – | ✅ | ✅ re-diagnosis |
| `GITHUB_APP_*` (the agent's identity) | – | ✅ fixes | ✅ PR diffs |
| `AGENT_CHECKPOINT_URL` | – | ✅ | – |
| `AGENT_CONSOLE_SECRET` | – | – | ✅ |
| `AGENT_DATABASE_URL`, `AGENT_CONFIG_DIR`, `AGENT_EVIDENCE_DIR` | ✅ | ✅ | ✅ |

Locally, one `.env` is fine. Each process uses only its own lines; the control plane and the console never build platform clients. With Docker Compose, split it into `.env.dataplane`, `.env.control` and `.env.console`, so each container *receives* only its own secrets ([docker-compose.yml](../docker-compose.yml)).

## `config/policy/autonomy.yaml` (policy bundle)
- **Rules:** `rules` are checked in order and the first match wins. Each rule matches on any of `agent`, `capability`, `env`, `pattern` (transient | code | infra | unknown | slow | manual) and `rule` (KEDB name); a field that's left out matches anything.
- **Level:** each rule gives an autonomy level 0–5. A level-3 rule also lists who may approve (`approval_from: [console]` or `[github_merge]`).
- **Limits:** `limits.max_reruns_per_day` and `max_actions_per_hour` apply on top of every level (a pipeline's `max_reruns_per_day` overrides the first). Exceeding one drops the action to L1.
- **Hash:** the file's sha256 travels in every signed request, so the control plane and the data plane must load the **same** file; the data plane refuses requests decided under another hash.

## `config/agents/*.yaml` (agent registry)
One manifest per agent: `name`, `kind` (`deterministic` or `llm`), `model_alias` (LLM agents), `capabilities` (what it may *request*; the policy denies anything else), `tools`, `risk_class` and `skills`. `GET /api/v1/agents` returns them as A2A-style cards.

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

Databricks data-plane fields: `git_url` (the pipelines repo; test runs use its branches) and `git_folders` (env → Git folder path, pulled by `code.sync`).

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

## `config/fix.yaml` (code-fix path)
| Field | Value in this repo | Meaning |
|---|---|---|
| `repo` | `karthik-np-ust/orders-pipelines` | The **pipelines** repo: the agent reads it and opens PRs against it |
| `base_branch` | `main` | Code the fix starts from (what failed in prod) |
| `promotion` | `[dev, qa, main]` | PR targets in order; the **last one is the human gate** (never merged by the agent) |
| `auto_merge` | `[dev, qa]` | Branches the agent may merge once checks are green (`code.merge`, L4) |
| `required_checks` | `notebook-lint` | Must be green on each PR (the pipelines repo's CI) |
| `allowed_paths` | `notebooks/`, `tests/` | The only paths a fix may change (the pipelines repo's path-guard enforces the same rule) |
| `context_paths` | `jobs/prod/*.json` | Read-only context for the model |
| `protected_patterns` | `DQAssertionError` | Count per file may not decrease (no deleting data-quality checks) |
| `forbidden_patterns` | `F.try_cast(`, `.try_cast(` | Platform knowledge: regex + hint, rejected before commit, hint fed back to the model |
| `require_test_change` | `true` | Legacy guard; the Recovery fix writer runs with it off because the **Testing agent** writes the tests |
| `max_files`, `max_file_chars`, `max_context_chars` | 6 / 40000 / 60000 | Limits |
| `patch_attempts` | 2 | Attempts per fix or test proposal, with validation errors fed back |
| `repair_attempts` | 2 | After failing Databricks tests or CI: fix again with the output (then escalate) |
| `max_wait_hours` | 48 | How long the prod PR may wait for the human merge |
| `deploy_workflow`, `max_concurrent` | – | Unused since the redesign (deploys are Git-folder syncs; each incident is its own graph) |

### Reconciliation checks (`reconcile:` per pipeline policy)
These read-only checks run after a successful rerun or repair (`data.profile` on the SQL warehouse). `{env}` is replaced with the incident's env:
```yaml
reconcile:
  - table: "{env}.silver.orders"
    keys: [order_id]                 # duplicates on these fail the check
    not_null: [order_id, store_id]
  - table: "{env}.gold.daily_sales"
    min_rows: 1
```
