# 02 · Architecture

## Components (current build)

```
┌──────────────────────────── pipeline-agent (Python service) ────────────────────────────┐
│  cli.py ─► runtime.py (wiring)                                                           │
│              │                                                                           │
│              ▼                                                                           │
│  monitor.py  Monitor.tick(): for each platform instance                                  │
│     │          list pipelines, list runs since watermark, upsert state, evaluate, act    │
│     ├─► sdk/classify.py   rule-based ErrorClass (core rules + plugin knowledge pack)     │
│     ├─► policy.py         decision table (below); thresholds, caps, circuit breaker      │
│     ├─► security/         redact.py (secrets/PII masking), audit.py (append-only log)    │
│     ├─► notify/           console, Teams (Adaptive Card), Slack, generic webhook         │
│     └─► store/db.py       SQLAlchemy: runs, actions, incidents, audit_log, watermarks    │
│                                                                                          │
│  sdk/  capability protocols, canonical models, PluginManifest, registry (entry points)   │
│  testing/  contract test kit every plugin must pass                                      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
          ▲ entry points "pipeline_agent.plugins"
          │
  plugins/agent-plugin-simulator   (reference plugin, demos, tests)
  plugins/agent-plugin-databricks  (Jobs API 2.2 via databricks-sdk)
  plugins/agent-plugin-snowflake   (Phase 6)
```

## Capability model
A plugin implements only the interfaces its platform supports ([src/pipeline_agent/sdk/capabilities.py](../src/pipeline_agent/sdk/capabilities.py)):

| Capability | Interface | Used for |
|---|---|---|
| MONITOR (required) | `RunSource.list_pipelines()`, `list_runs(since, active_only)` | Polling |
| CANCEL, RERUN | `RunController.cancel(run)`, `rerun(pipeline, params)` | Threshold and transient handling |
| LOGS | `LogProvider.failure_context(run)` | Classification, incidents, LLM context (Phase 2) |
| COST | `CostProvider.costs(since)` | FinOps view (Phase 4) |
| CODE | `CodeLocator.code_ref(pipeline)` | Fix flow (Phase 3) |
| EVENTS | `EventSource.parse_webhook(...)` | Push instead of polling (Stage 2) |

The policy engine checks capabilities before acting. A platform without CANCEL/RERUN gets an escalation on a threshold breach, and without CODE an incident is never fix-eligible.

## Canonical data model
[src/pipeline_agent/sdk/models.py](../src/pipeline_agent/sdk/models.py):
- `Pipeline`, `Run`, `RunRef`, `FailureContext`, `TaskFailure`, `CostRecord`, `CodeRef`, `RunEvent`
- `Env`: dev, qa, prod
- `RunStatus`: queued, running, succeeded, failed, cancelled, skipped, unknown
- `ErrorClass`: transient, code, infra, unknown

Every model keeps the original platform payload in `raw`.

## Decision table ([src/pipeline_agent/policy.py](../src/pipeline_agent/policy.py))

| Observed | Condition (checked in order) | Decision |
|---|---|---|
| Running over threshold | already handled | none |
| | env not in `act_in_envs` | notify |
| | platform lacks CANCEL/RERUN | escalate |
| | reruns in last 24h ≥ `max_reruns_per_day` | escalate |
| | circuit breaker open | escalate |
| | otherwise | **cancel + rerun** |
| Failed | already handled (incident or action exists) | none |
| | env not in `act_in_envs` | notify |
| | transient, reruns(transient) < `transient_retries`, breaker closed | **rerun** |
| | transient otherwise | escalate |
| | code | **open incident** (fix-eligible if `auto_fix` and CODE) |
| | infra / unknown | escalate |

**Threshold:** `max_duration_seconds` if set. Otherwise p95 of the last 30 successful runs × `baseline_multiplier`, once `baseline_min_samples` runs exist. With neither, there is no threshold.

**Circuit breaker:** stops all automatic cancel/rerun once `max_actions_per_hour` real actions (dry-run actions excluded) have happened across all platforms in the last hour. This protects the Free Edition fair-use quota.

## Polling and idempotency
- **Window:** each tick reads runs that are active now, or that ended at or after `watermark − poll_overlap_minutes`. The first poll uses `initial_lookback_minutes`.
- **Databricks:** the plugin also scans runs that started up to `max_run_lookback_hours` before the window, so long runs that finish inside the window aren't missed.
- **Dedupe:** every decision is recorded as an action or incident keyed by `platform:pipeline:run`, so a run is never handled twice. Logs are fetched once per failed run.
- **Isolation:** an exception in one platform is logged and audited, and the other platforms keep polling.

## Environments in one workspace (Free Edition)
| Branch | Bundle target | Job name | Data | Trigger |
|---|---|---|---|---|
| `dev` | dev | `[dev] orders_pipeline` | catalog `dev` | on demand (CI smoke run) |
| `qa` | qa | `[qa] orders_pipeline` | catalog `qa` | on demand (CI smoke run) |
| `main` | prod | `orders_pipeline` | catalog `prod` | schedule |

The Databricks plugin maps the name prefix to `Env` (`env_prefixes` config). Fallback when catalogs can't be created: `layout=schema` → `workspace.<env>_bronze.orders`.

## Planned flows (not built yet)

**Phase 2: LLM triage (built).** After an incident is created, `Monitor.run_triage` calls `Triage.diagnose`, which goes through `LLMGateway.chat`:
1. budget check against the `llm_calls` ledger
2. redaction of every message
3. model fallback
4. ledger write

The model answers through a single `report_diagnosis` tool, and its JSON is validated into `Diagnosis`. The result is stored on the incident and sent as a follow-up notification. Budget stop → incident escalated. LLM outage → audit `llm_unavailable`; monitoring continues. The rule-based class still drives actions.

**Phase 3: fix flow (built).** [fix/flow.py](../src/pipeline_agent/fix/flow.py) is a state machine persisted in `fix_attempts`. Each monitor tick moves every active attempt at most one step, so the flow survives restarts and can wait for CI, deploys or a human.

```
PLANNED ─ read code at main (GitHub API) → LLM writes fix + test → guards → commit fix/incident-N → PR → dev
PR_OPEN(dev)  ─ required checks green → agent merges (auto_merge) ─► DEPLOYING(dev) ─ deploy.yml run green
PR_OPEN(qa)   ─ PR dev → qa; checks green → agent merges if rules allow, else waits for a human
DEPLOYING(qa) ─ deploy green → PR qa → main
PR_OPEN(main) ─ checks green → "Approval needed" notification; the agent NEVER merges this
              ─ human merges ─► DEPLOYING(main) ─ prod deploy (prod Environment approval) green
              ─► rerun prod job ─► VERIFYING ─ rerun succeeded ─► RESOLVED (incident resolved)
CI failure on the fix PR → REPAIR: read the CI job log, LLM fixes, new commit on the same PR (max repair_attempts)
Repairs exhausted, or a failing promotion/deploy → FAILED (PR comment with failures). PR closed unmerged → REJECTED.
No progress for max_wait_hours → FAILED. All three escalate the incident.
```

**Starting a fix:** after triage, when the incident is fix-eligible (`auto_fix` + CODE capability), still `open`, and the diagnosis `fix_type` is `code_change`/`data_fix`. You can also start one by hand with `pipeline-agent fix <incident>`. At most `max_concurrent` fixes run at once.

**Patch guards** ([fix/patch.py](../src/pipeline_agent/fix/patch.py)) run before anything is committed:
- allowed paths only
- Python compiles
- protected patterns (`assert_not_null(`) never decrease
- a test must change
- no secrets
- file count and size limits

The model gets one retry with the validation errors fed back. Guards also run **pyflakes** for undefined names (e.g. a test using `F` without importing it).

**Self-repair:** when CI fails on the fix PR, the agent downloads the failed job's log (GitHub App: Actions read). It keeps the failure lines (`E …`, `FAILED …`, the summary) and sends them to the model with the branch's current files. The model's corrected files are pushed as a new commit on the same PR, with a PR comment explaining what was fixed. The guards check the **combined** change against `main`, so a repair can't do what the first fix wasn't allowed to do (e.g. delete a DQ check).

**Idempotency:** branch creation moves an existing branch, and PR creation reuses an open PR. A step that fails mid-way (network) is retried on the next tick without duplicates.

**Phase 4: API and Ops Console.** FastAPI (REST + SSE) and React/Fluent UI.

**Stage 2/3 hosting:** see [07-future-improvements.md](07-future-improvements.md).
