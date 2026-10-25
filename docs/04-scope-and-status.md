# 04 · Scope and status

Last updated: 2026-10-25.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 0 Spike | Check Free Edition: billing table, catalogs, service principals, bundle deploy; OpenRouter tool calling | ✅ **Done 2026-10-25.** All API checks pass (results below). Two corporate-network blockers found for deploys. |
| 0b Sample jobs + fault kit | Bundle with 3 jobs × 3 envs, synthetic data, 14 fault scenarios, setup/inject/reset/deploy scripts, CI/CD workflows | ✅ Built. ◐ Workspace setup partial (dev catalog, schemas, volume, secret scope). **Data upload and job deploy are blocked by Zscaler from this laptop.** |
| 1 Plugin SDK + monitor | Capabilities, models, registry, contract kit, simulator, Databricks plugin, policy engine, monitor, store, audit, notifier, redaction, CLI | ✅ Built and tested. Databricks plugin **connected to the real workspace** (auth, jobs API, billing table). |
| 2 LLM triage | LLM gateway (OpenRouter), budgets, ledger, model fallback, structured diagnosis, prompt-injection flagging | ✅ **Built and tested** (97 tests total). Live run against OpenRouter: 2 incidents diagnosed correctly, with fallback after a provider 502. |
| 3 Fix flow | Repo tools, patch branch, dev → qa promotion PRs, human approval gate, prod rerun | ⏳ Not started (CI side ready: ci.yml, deploy.yml, CODEOWNERS, path guard) |
| 4 API + Ops Console | FastAPI + SSE, React/Fluent UI MVP pages, cost dashboard | ⏳ Not started |
| 5 Hardening | Prompt-injection tests, Docker packaging verified, guardrail review | ◐ Partial: kill switch, dry run, circuit breaker, redaction, injection heuristic, Dockerfile |
| 6 Snowflake + OpenLineage | Second plugin, proving zero core changes | ⏳ Not started |

## Phase 0 results (2026-10-25, `python scripts/phase0_checks.py --create-catalog`)
| Check | Result | Decision |
|---|---|---|
| Databricks auth (PAT) | ✅ | |
| Jobs API | ✅ | |
| `system.billing.usage` | ✅ readable: 7,858 rows, 2026-01-15 → 2026-10-23 | `use_billing_table: true` for `dbx-free` |
| `CREATE CATALOG` | ✅ `dev` created | Catalog-per-env layout (no schema fallback needed) |
| Workspace service principals API | ✅ available | Production plan: SP for agent and CI (not done for the demo) |
| Secret scopes API | ✅ | `partner-dev` created |
| Databricks CLI | ❌ global one is the legacy 0.18; the current CLI download is **blocked by the corporate proxy** | Local deploys use `scripts/deploy_sdk.py`; CI uses the real CLI |
| OpenRouter key | ✅ free tier, 50 free requests/day | `per_day_requests: 45` budget |
| OpenRouter tool calling | ✅ (`recursive/flint-v1.0:free`) | Triage uses tool calling with JSON fallback |
| Python TLS on corporate network | ❌ → ✅ Zscaler TLS inspection broke `requests` | `truststore` makes Python use the Windows certificate store |
| File upload to UC volume (`PUT /api/2.0/fs/files`) | ❌ **Blocked by Zscaler** (`PermissionDenied: Internet Security by Zscaler`) | Open decision, see below |

### Open decision: deploys and data seeding from this laptop are blocked
JSON API calls work: monitoring, SQL, secrets, jobs, cancel/rerun. Binary uploads (data files, and probably workspace file imports) are blocked by corporate policy. Options:
1. **Run deploy + seeding from GitHub Actions** (recommended: it's the planned CI path anyway). Workflows need `DATABRICKS_HOST`/`DATABRICKS_TOKEN` secrets.
2. Ask IT to allowlist the Databricks workspace host for uploads.
3. Run setup/deploy from a machine that isn't behind Zscaler.

## What exactly works today
- **Monitoring** of any number of platform instances (simulator + real Databricks workspace), with per-platform failure isolation.
- **Threshold handling:** explicit or p95 baseline, cancel + rerun, a cap of 2/day, then escalation.
- **Failure classification** from YAML rules (core + plugin knowledge packs) into transient, code, infra or unknown.
- **Transient reruns** with a retry cap. **Incidents** for code failures (fix-eligible with `auto_fix`). **Escalation** for infra/unknown. **Notify-only** for dev/qa.
- **LLM triage (Phase 2):**
  - structured diagnosis (summary, root cause, class, confidence, component, fix type, suggested fix, evidence)
  - stored on the incident and sent as a follow-up notification
  - the rule-based class still drives actions; the LLM explains
- **LLM budgets:** per-day requests and cost, per-incident tokens and cost. On a budget stop the incident is escalated (scenario 14).
- **LLM ledger** (`llm_calls`): model, tokens, cost, latency, redactions, errors. Shown by `pipeline-agent llm`.
- **Model fallback** on provider errors or rate limits.
- **Prompt-injection defence:** untrusted-logs framing, single-tool answers, and a heuristic flag that overrides the model. Flagged incidents get a critical notification (scenario 12).
- **Safety controls:** kill switch, dry run, circuit breaker. **Redaction** before storage, notification, audit and every LLM call.
- **CLI:** `run`, `once`, `check`, `plugins`, `incidents [--show ID]`, `triage ID`, `llm`, `audit`.
- **Corporate network support:** OS trust store through `truststore`; `.env` loaded for `env://` references.

## Verified vs not verified
| Item | Verified how |
|---|---|
| Core, simulator, LLM | 97 automated tests + live CLI runs (simulator + real OpenRouter) |
| Databricks plugin | Fake-client tests, plus a live `check` against the real workspace and a live billing-table cost query |
| Real job monitoring end-to-end | **Not yet:** demo jobs aren't deployed (upload block) |
| Bundle deploy (CLI or `deploy_sdk.py`) | **Not yet:** CLI unavailable locally; `deploy_sdk.py` written but not run (needs workspace uploads) |
| Sample pipeline Spark tests | Skipped locally (no Java); run in CI |
| Docker image / compose | Not built (no Docker on this machine) |

## Deviations from the approved plan (and why)
| Plan said | Built | Reason |
|---|---|---|
| Postgres in all stages | SQLite by default; Postgres through `AGENT_DATABASE_URL` (+ `psycopg` extra, compose file) | No Docker locally; same SQLAlchemy code for both |
| APScheduler | Simple poll loop (`pipeline-agent run`) | One periodic job doesn't need a scheduler library |
| `agent/` package folder | `src/pipeline_agent/` | Standard src layout |
| `faults/*.patch` git patches | `faults/*.yaml` find/replace edits | Robust on Windows line endings; a test checks each edit still applies |
| Separate `seed_data.py`, `break_env.py` | Folded into `inject_fault.py` / `_common.py` | One entry point per scenario |
| Scenario 11: revoke a grant | Delete a secret (`Secret does not exist`) | On Free Edition the only user owns everything |
| Scenario 12: injection in `notes` | Injection in the `qty` value | It then appears verbatim in the CAST error, so it really reaches the agent |
| LangGraph for triage | Plain gateway + triage module | Triage is one structured call; LangGraph is kept for the multi-step fix flow (Phase 3) |
| Local `databricks bundle deploy` | `scripts/deploy_sdk.py` (same bundle files) | CLI download blocked by the corporate proxy; CI still uses bundles |
