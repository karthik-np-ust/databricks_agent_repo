# 04 · Scope and status

Last updated: 2026-10-27.

## 2026-10-27: LangGraph re-architecture (in progress)

The agent is being rebuilt to mirror the "Agentic AIOps for DataOps v2" reference architecture:
- a LangGraph multi-agent **control plane**: Monitoring, Triage, Recovery, a new **Testing** agent and Reconciliation, plus planner, policy with autonomy levels L0–L5, registry, Known Error DB and evidence store;
- **signed ActionRequests** across a trust boundary;
- a **data-plane worker** that alone holds the Databricks credentials (execution service, execution ledger, capability router).

This reverses the earlier "no LangGraph" decision (Deviations table below). Since then, the scope has grown from one linear fix flow to several cooperating agents, human interrupts and replay-safe execution.

**Pipelines moved to their own repo** (`karthik-np-ust/orders-pipelines`), as in a real customer environment:
- They are Databricks notebooks written as plain top-level PySpark cells.
- The user checks them out as one Git folder per environment and creates the jobs.
- The agent never deploys pipelines.
- Tests are Databricks test notebooks run inside Databricks; there is no local Spark.

| Phase | Scope | Status |
|---|---|---|
| R0 Environment checks | Git folders, `jobs.submit` with `git_source`, `dbutils.notebook.run` on serverless, `repair_run`, SQL warehouse | ✅ **All pass 2026-10-27** (`phase0_checks.py --redesign`, results below) |
| R1 Pipelines repo | `orders-pipelines`: 5 notebooks, 5 test notebooks, setup/seed/reset notebooks, job specs per env, faults, CI. Agent repo: `sample_pipelines/`, deploy and seeding scripts, and `deploy.yml`/`demo-ops.yml` removed; config points at the new repo | ✅ Built 2026-10-27 (local repo, lint clean). ⏳ User: push to GitHub, Git folders, jobs ([15](15-databricks-setup.md)) |
| R2 Foundations | DB tables, Ed25519 signing, policy bundle + autonomy matrix, agent manifests, KEDB, evidence store, model gateway wrapper | ✅ Built 2026-10-27 (27 new tests, 166 total). Not wired into the running agent yet (R3/R4) |
| R3 Data plane | Executor, ledger, capability router, observer; Databricks adapter: repair, code.sync, tests.run, smoke_run, data.profile | ✅ Built 2026-10-27 (18 new tests, 184 total; live read-only tick + one signed `tests.run` against the workspace). `pipeline-agent dataplane` |
| R4 Control plane, runtime path | Incident graph, Monitoring/Triage/Planner/Recovery/Reconciliation, approvals by interrupt | ✅ Built 2026-10-27 (14 new tests, 198 total; verified with the two real CLI processes over the simulator). `pipeline-agent control`, `pipeline-agent approvals`. Runs next to the legacy `run` until R6 removes it |
| R5 Code-fix path | Recovery fix + Testing agent, GitHub promotion, Git-folder sync, prod repair | ⏳ |
| R6 Console | Approvals, ledger, KEDB, registry, evidence; old monitor/fix flow deleted | ⏳ |
| R7 Packaging + docs | 4-service compose, docs rewrite | ⏳ |

### R0 results (2026-10-27, `python scripts/phase0_checks.py --redesign`)
| Check | Result | Decision |
|---|---|---|
| Git folders via Repos API | ✅ all 3 (`dev`, `qa`, `main`, same head commit). `workspace.get_status` reports them as `DIRECTORY` and `repos.list` doesn't list them, but `repos.get(object_id)` works | `code.sync` = `repos.update(object_id, branch)`; look the folder up by path, then by object id |
| Test notebook from a GitHub branch (`jobs.submit` + `git_source`) | ✅ `test_transform_silver` passed, including `dbutils.notebook.run` of a sibling notebook | `tests.run` runs the fix branch directly from GitHub; no scratch Git folder is needed |
| Test notebook from the dev Git folder | ✅ passed | `dbutils.notebook.run` works on serverless |
| `jobs.repair_run` | ✅ accepted. On a successful run it is a no-op (no task re-ran) | `pipeline.repair` re-runs only the failed tasks |
| SQL warehouse | ✅ | `data.profile` uses statement execution |
| `system.lakeflow.job_run_timeline` | ✅ readable | The observer can use it for history and baselines |

**Until R5:**
- The old fix flow can open a PR in the pipelines repo but cannot deploy it, because there is no `deploy.yml` any more.
- Docs 02, 03, 05, 08, 10 and 12 still describe `sample_pipelines/`. They are rewritten in R7.

## Phase status (original build, before the redesign)

| Phase | Scope | Status |
|---|---|---|
| 0 Spike | Check Free Edition: billing table, catalogs, service principals, bundle deploy; OpenRouter tool calling | ✅ **Done 2026-10-25.** All API checks pass (results below). Two corporate-network blockers found for deploys. |
| 0b Sample jobs + fault kit | Bundle with 3 jobs × 3 envs, synthetic data, 14 fault scenarios, setup/inject/reset/deploy scripts, CI/CD workflows | ✅ Built. ◐ Workspace setup partial (dev catalog, schemas, volume, secret scope). **Data upload and job deploy are blocked by Zscaler from this laptop.** |
| 1 Plugin SDK + monitor | Capabilities, models, registry, contract kit, simulator, Databricks plugin, policy engine, monitor, store, audit, notifier, redaction, CLI | ✅ Built and tested. Databricks plugin **connected to the real workspace** (auth, jobs API, billing table). |
| 2 LLM triage | LLM gateway (OpenRouter), budgets, ledger, model fallback, structured diagnosis, prompt-injection flagging | ✅ **Built and tested** (97 tests total). Live run against OpenRouter: 2 incidents diagnosed correctly, with fallback after a provider 502. |
| 3 Fix flow | Patch writer + guards, GitHub client, dev → qa promotion PRs, human approval gate, prod rerun + verify | ✅ **Built and tested** (fake GitHub + fake LLM, 123 tests). ⏳ Live run needs the agent's GitHub bot token ([13](13-github-setup.md#12-agent-identity-for-phase-3)). |
| 4 API + Ops Console | FastAPI + React/Fluent UI console: 12 screens, RBAC, audited actions, runtime kill switch, cost dashboards | ✅ **Built** (11 API tests; UI type-checked and built; smoke-tested on real data). Demo auth only; Entra ID OIDC next. |
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
| Real job monitoring end-to-end | ✅ 2026-10-26: fault `bad_cast` injected in prod from GitHub. The agent detected the failed run, classified it `code`/`cast_invalid_input`, opened incident #1 (fix-eligible) and got an LLM diagnosis naming `transform_silver` and the value `'abc'` (95% confidence). The old dev failure was handled as notify-only. |
| Bundle deploy | ✅ through GitHub Actions (`deploy.yml`) for dev, qa and prod, including smoke runs. `deploy_sdk.py` is still not run (local uploads blocked). |
| Sample pipeline Spark tests | ✅ pass in CI (`pipeline-tests`, PR #1); skipped locally (no Java) |
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
| LangGraph for triage and the fix flow | Plain gateway + triage module; DB-backed state machine (`fix_attempts`) | The flow is linear with long external waits (CI, deploys, humans). A persisted state machine advanced per tick is simpler, resumable across restarts and fully unit-testable, with no extra framework. **Superseded 2026-10-27** by the LangGraph re-architecture (top of this page). |
| Local `databricks bundle deploy` | `scripts/deploy_sdk.py` (same bundle files) | CLI download blocked by the corporate proxy; CI still uses bundles |
