# 10 · Demo runbook

## A. Credential-free demo (simulator)
```powershell
.\.venv\Scripts\pipeline-agent check
.\.venv\Scripts\pipeline-agent once      # t=0: all runs just started
# wait ~60s
.\.venv\Scripts\pipeline-agent once      # code incident, transient rerun, permission escalation
# wait ~5 min
.\.venv\Scripts\pipeline-agent once      # sales_report over 300s threshold -> cancel + rerun
.\.venv\Scripts\pipeline-agent incidents
.\.venv\Scripts\pipeline-agent audit
```
To start again, delete `.sim_state.json` and `agent_state.db`, or run `python scripts/reset_demo.py --skip-workspace --agent-state`.

## B. Databricks Free Edition demo

### Prerequisites
- Databricks Free Edition account, and a personal access token (User settings → Developer → Access tokens).
- The SQL warehouse ID (SQL Warehouses → Serverless Starter Warehouse → Connection details).
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) (for bundles).
- A GitHub repo with this code: public, or UST GitHub Enterprise ([05](05-environment-restrictions.md#github)).
- `.env` from `.env.example` with `DATABRICKS_HOST`, `DATABRICKS_TOKEN` and `DATABRICKS_WAREHOUSE_ID`.

### Phase 0 checks
Automated: `python scripts/phase0_checks.py [--create-catalog]`. Results from 2026-10-25 are in [04-scope-and-status.md](04-scope-and-status.md#phase-0-results-2026-10-25-python-scriptsphase0_checkspy---create-catalog). Manual equivalents:

| # | Check | How | If it fails |
|---|---|---|---|
| 1 | Billing table readable | SQL editor: `SELECT * FROM system.billing.usage LIMIT 5` | Keep `use_billing_table: false` (duration estimate) |
| 2 | Catalogs can be created | `CREATE CATALOG IF NOT EXISTS dev` | Use `--layout schema` and bundle `--var layout=schema --var catalog=workspace` |
| 3 | Workspace service principals | Settings → Identity and access → Service principals | Use a PAT for a dedicated bot user (or your own, for the demo) |
| 4 | Bundle deploy works | `cd sample_pipelines; databricks bundle validate -t dev; databricks bundle deploy -t dev` | Check `environment_version` vs `client` in jobs.yml for your CLI version |
| 5 | `__file__` imports work on serverless | Run `[dev] orders_pipeline`; `ingest_bronze` must import `common` | Package `common` as a wheel (bundle `artifacts`) |
| 6 | Secret scopes | `databricks secrets create-scope partner-dev` | Drop scenario 11 or use an env-var-based check |
| 7 | OpenRouter tool calling (Phase 2) | One chat completion with `tools` returning `usage` | Pick another model in config |

### Setup
> **Corporate laptop (Zscaler):** uploads to Databricks are blocked, so steps 1–2 below fail from the UST network. Run them from GitHub Actions or a non-corporate machine. See [05](05-environment-restrictions.md#corporate-network-ust-laptop-zscaler-found-2026-10-25).
> Without the Databricks CLI, `python scripts/deploy_sdk.py -t dev|qa|prod` deploys the same bundle files (and `--destroy` removes them before switching to real bundle deploys).

```powershell
.\.venv\Scripts\pip install databricks-sdk pyyaml   # already installed with the Databricks plugin
.\.venv\Scripts\python scripts\setup_workspace.py   # or --layout schema
cd sample_pipelines
databricks bundle deploy -t dev
databricks bundle deploy -t qa
databricks bundle deploy -t prod
cd ..
git tag demo-baseline; git push origin demo-baseline   # reset point for reset_demo.py --git
```
In `config/platforms.yaml`, set `dbx-free.enabled: true` and `host`. Optionally disable `sim-demo`. Then:
```powershell
.\.venv\Scripts\pipeline-agent check    # dbx-free: health=ok (authenticated; N jobs visible)
$env:AGENT_DRY_RUN="true"; .\.venv\Scripts\pipeline-agent run   # watch decisions first
```

### GitHub setup for the branch-per-environment flow
1. Create branches `dev` and `qa` from `main`.
2. Add branch protection on `qa` and `main`: require PR, Code Owner review and the `ci` checks; block force pushes (admins may bypass for `reset_demo.py --git`).
3. Create Environments `dev`, `qa` and `prod`, each with secrets `DATABRICKS_HOST` and `DATABRICKS_TOKEN`. Add **required reviewers** on `prod`.
4. Set the repo variable `AGENT_BOT_LOGIN` (enables the path guard) and replace the CODEOWNERS placeholders.

## B2. LLM triage (Phase 2)
```powershell
# .env: OPENROUTER_API_KEY=...  AGENT_LLM_ENABLED=true
.\.venv\Scripts\pipeline-agent llm            # budgets, today's usage, provider key status
.\.venv\Scripts\pipeline-agent once           # new incidents are diagnosed automatically
.\.venv\Scripts\pipeline-agent incidents --show 1
.\.venv\Scripts\pipeline-agent triage 1       # re-run the diagnosis (uses 1+ request)
```
The free tier allows 50 requests/day. Each incident uses 1 request, or 2 if the first model fails.

## C. Fault scenario catalog
`python scripts/inject_fault.py list` shows them all. Reset between scenarios with `python scripts/reset_demo.py`.

| # | Command | Expected agent behaviour (acceptance) | Agent support today |
|---|---|---|---|
| 1 | `inject_fault.py slow_report` | Cancel + rerun after 300s. Rerun runs are also slow (same params) → 2nd rerun → 3rd breach escalates. | ✅ |
| 2 | `inject_fault.py flaky_partner` | Rerun once. If it fails again, escalate. No incident on success. | ✅ |
| 3 | `inject_fault.py bad_cast` | Incident `code` / `cast_invalid_input`, fix-eligible | ✅ detection · ⏳ fix (Phase 3) |
| 4 | `inject_fault.py zero_units` | Incident `code` / `divide_by_zero` | ✅ · ⏳ |
| 5 | `inject_fault.py duplicate_keys` | Incident `code` / `merge_duplicate_source_rows` | ✅ · ⏳ |
| 6 | `inject_fault.py schema_drift` | Incident `code` / `unresolved_column` | ✅ · ⏳ |
| 7 | `inject_fault.py null_keys` | Incident `code` / `dq_assertion`. The fix must quarantine rows, not drop the check. | ✅ · ⏳ |
| 8 | `inject_fault.py typo_column --push` | Incident `code` / `unresolved_column`; the fix cites the bad commit | ✅ · ⏳ |
| 9 | `inject_fault.py hardcoded_catalog --push` | Incident `code` / `table_not_found` | ✅ · ⏳ |
| 10 | `inject_fault.py syntax_error --push` | Incident `code` / `python_syntax_or_import` | ✅ · ⏳ |
| 11 | `inject_fault.py missing_secret` | **Escalate** `infra` / `secret_missing`. No rerun, no PR. | ✅ |
| 12 | `inject_fault.py prompt_injection` | Incident `code`, **flagged suspicious** (critical notification, audit `suspicious_content`). Phase 3: the agent must ignore it and the path guard blocks workflow edits. | ✅ flag · ⏳ fix flow |
| 13 | `inject_fault.py secret_in_logs` | The fake `dapiFAKE…` token is `[REDACTED:DATABRICKS_TOKEN]` in the incident, notification and audit | ✅ |
| 14 | set `per_day_requests: 0` in budgets.yaml, then any code fault | Agent stops at the budget, escalates, audit `llm_budget_stop` | ✅ |

Release faults (8–10) commit straight to `main`, which needs admin bypass of branch protection. That's deliberate: it simulates a bad hotfix. Undo them with `reset_demo.py --git` (asks before force-pushing).

### 20-minute live demo order
1 → 2 → 3 → 11 → 13. Show `pipeline-agent incidents` and `audit` after each. In Phase 3+, scenario 3 runs the whole dev → qa → approval → prod flow.
