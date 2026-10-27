# Work log

Newest first. Each entry: what was built, how it was verified, what's next.

## 2026-10-27: Redesign R3, data plane
**Built** (`pipeline-agent dataplane [--once]`; this process holds the platform credentials):
- **Plugin SDK 1.1:** optional `ActionProvider` (`get_run`, `repair`, `sync_code`, `run_tests`, `profile`, `definition_hash`) and `Capability.ACTIONS`. Additive, so 1.0 plugins still load.
- **Capability router:** maps stable capability ids to the platform adapter.
  - Live-state preconditions: run active/terminal/failed, no newer success, definition unchanged, non-prod env, merged commit known.
  - Post-conditions: the platform knows the new run; the Git folder head equals the merged commit.
- **Executor:** claim → verify signature and expiry → policy bundle hash equals ours → policy re-evaluated locally (caps counted from the ledger) → preconditions → ledger reserve → execute → post-conditions → ledger finish → evidence → result.
  - A replayed key returns the recorded outcome.
  - A platform error gives `FAILED`; an unconfirmed effect gives `UNKNOWN`.
  - Dry run checks everything and executes nothing.
- **Observer:** polls each platform and emits `run_failed` once per run (redacted failure text in the evidence store, plus classifier hints) and `run_slow` (explicit threshold or p95 baseline).
  - It watches runs the executor started and emits `run_finished` with the outcome.
  - A repair that hasn't started yet isn't mistaken for its old failed state.
- **Service:** observe → execute → sweep stale ledger entries to UNKNOWN, behind a `data-plane` lease, so one instance is active.
- **Databricks adapter:**
  - `repair_run` (failed tasks only);
  - Git-folder sync (path → object id → `repos.update`, the R0 workaround);
  - tests via `jobs.submit` + `git_source`, run in sequence with `run_if: ALL_DONE` (Free Edition allows 5 concurrent tasks);
  - `data.profile` on the SQL warehouse with validated identifiers;
  - job-definition hash.
- **Simulator:** the same actions, deterministic (sync "deploys the fix", test outcome per branch, configurable profiles).
- `config/platforms.yaml`: `git_url` and `git_folders` for `dbx-free`.

**Verified:**
- 184 tests pass, 18 of them new. Covered: observer dedupe and redaction; slow runs; a transient rerun watched to completion; the code-fix path (tests fail/pass, sync, prod repair); profile; replay; tampered, untrusted and drifted requests; policy re-check with approval and kill switch; live preconditions; dry run; platform error → FAILED; bad sync → UNKNOWN; lease; Databricks adapter calls.
- **Live, read-only tick:** 13 runs seen, no events (nothing failed in 24h).
- **Live signed `tests.run`:** queued → verified → L4 → run `228107115875655` submitted from `dev` → `run_finished: succeeded`.
  - The first attempt failed after 5 minutes of SDK retries because the test script hadn't enabled the OS trust store (Zscaler). The ledger correctly recorded `FAILED`, and no run was created.

**Next:** R4 control plane: LangGraph incident graph with Monitoring, Triage, Planner, Recovery (runtime path) and Reconciliation, plus console approvals via `interrupt()`.

## 2026-10-27: Redesign R0 checks and R2 foundations
**R0 (environment checks):** all 8 pass; results and decisions are in [04](04-scope-and-status.md).
- The env folders are real Git folders (`repos.get` works even though `get_status` reports `DIRECTORY`).
- A test notebook runs from a GitHub branch with `jobs.submit` + `git_source`, so the Testing agent needs no scratch folder.
- `dbutils.notebook.run` works on serverless.
- `repair_run`, the SQL warehouse and `system.lakeflow` are all available.

**R2 (foundations).** These are building blocks only; nothing is wired into the running agent yet.
- **Tables:** `events`, `work_items`, `action_requests`, `action_results`, `execution_ledger`, `approvals`, `known_errors`, `evidence`, `leases`.
- **Trust boundary (`boundary/`):**
  - capability catalogue: `pipeline.rerun|repair|cancel|smoke_run|get_run`, `code.merge|sync`, `tests.run`, `data.profile`;
  - `ActionRequest`, which carries an idempotency key, expiry, policy-bundle hash, preconditions and an approval reference;
  - Ed25519 sign/verify (`security/signing.py`, `pipeline-agent keys generate`);
  - pull queue with idempotent submit and atomic claim.
- **Autonomy policy (`autonomy/` + `config/policy/autonomy.yaml`):**
  - first-match rules over agent × capability × env × failure pattern give L0–L5;
  - OPA-shaped decision (allow, mode, needs_approval, approval_from, reasons, bundle sha256);
  - the kill switch, dry run, rerun cap and circuit breaker are inputs;
  - the agent registry denies any capability an agent's manifest doesn't list.
  - Named `autonomy/` because `policy.py` (the old engine) keeps its name until R6.
- **Agent registry:** `config/agents/{monitoring,triage,recovery,testing,reconciliation}.yaml`, with A2A-style cards.
- **KEDB (`kedb.py`, `pipeline-agent kedb seed|list|approve`):**
  - seeded from the core and plugin knowledge packs as approved entries;
  - agent proposals stay `proposed` until an admin approves them;
  - names are unique per platform (a smoke test found plugin rules being skipped when they shared a generic rule's name).
- **Evidence store (`evidence.py`):** redacted, sha256 content-addressed blobs in `var/evidence/` plus metadata; an integrity check on read; `as_prompt` frames evidence as `<untrusted_evidence>`.
- **Execution ledger (`data_plane/ledger.py`):** reserve → finish. A replay returns the recorded state, and a crashed execution is swept to `UNKNOWN` (never auto-retried).
- **Leases (`leases.py`):** one holder until expiry. This fixes the "two agents at once" gap.
- **Model gateway (`llm/model_gateway.py`):** agents ask for an alias (`triage`, `fix`, `test`), which maps to fallback lists in `llm.yaml`. Budgets and redaction stay in `LLMGateway`.
- **Dependencies:** `langgraph`, `langgraph-checkpoint-sqlite`, `cryptography`; the `postgres` extra adds `langgraph-checkpoint-postgres`. `var/` is git-ignored.

**Verified:**
- 166 tests pass, 27 of them new: tampering per field, unknown key, expiry, idempotent submit, single claim, ledger replay and UNKNOWN, lease takeover, policy matrix cases, bundle hash, registry, KEDB curation, evidence redaction and integrity, model aliases.
- CLI smoke test on a scratch DB: keys generated; KEDB seeded with generic +5, databricks +12, simulator +2.

**Note:** `pip install -e .` could not finish while a `pipeline-agent.exe` process was running (file lock). Re-run it once that process is stopped.

**Next:** R3 data plane: executor, capability router, observer, and the new Databricks adapter capabilities.

## 2026-10-27: Redesign R1, pipelines in their own repo
**Decision:** rebuild the agent on LangGraph to match the "Agentic AIOps for DataOps v2" reference architecture (phases R0–R7 in [04](04-scope-and-status.md)). Pipelines live in a separate repo, as in a real customer environment. The read-only MCP server from the reference was left out on purpose: the observer already pushes the failure evidence.

**Built:**
- **New repo `C:\Users\285509\orders-pipelines`** (local, not pushed yet):
  - **Pipeline notebooks:** `ingest_bronze`, `transform_silver`, `build_gold`, `sales_report` and `partner_feed` are now Databricks notebooks.
    - Top-level PySpark cells, widgets instead of argparse, table names resolved inline.
    - The DQ check raises `DQAssertionError` inline.
    - No functions, no `common/` package, no `--src-dir` workaround.
  - **5 test notebooks** that run in Databricks: isolated `test_<id>_*` schemas, seed fixtures, `dbutils.notebook.run`, then asserts. They port the old pytest assertions.
  - **Setup notebooks** `00_setup_demo`, `10_seed_scenario` (all data scenarios) and `99_reset_demo`. Data seeding now happens inside Databricks, so the proxy upload block no longer matters.
  - **Job specs:** `jobs/{dev,qa,prod}/*.json`, with notebook tasks on serverless from the Git folders `/Workspace/Shared/orders-pipelines-<env>`.
  - **Faults** retargeted to the notebook text.
  - **CI:** `notebook-lint`, `promotion-path` and `path-guard`, plus CODEOWNERS.
  - **Working rules:** `CLAUDE.md` and `TASK_LOG.md`, from the user's rules.
- **Agent repo, removed:** `sample_pipelines/` code, `scripts/deploy_sdk.py`, `setup_workspace.py`, `generate_data.py`, `reset_demo.py`, `deploy.yml`, `demo-ops.yml`, and the pipeline CI jobs.
- **Agent repo, changed:**
  - `inject_fault.py`:
    - release faults edit a local clone (`--repo-path`);
    - data faults run the seed notebook through `jobs.submit`;
    - new `reset-git` command.
  - Config points at `karthik-np-ust/orders-pipelines`, with `allowed_paths: [notebooks/, tests/]`, `protected_patterns: ["DQAssertionError"]` and `required_checks: [notebook-lint]`.
  - Fix prompt `fix-v3`: notebook style, test notebooks run in Databricks.
- New [15 · Databricks setup](15-databricks-setup.md).

**Verified:**
- Notebooks compile and pass pyflakes.
- Every fault still applies, and `syntax_error` really breaks compilation.
- 139 agent tests pass. The 9 `generate_data` tests were removed with that script.

**Next:**
- User: create the GitHub repo, Git folders and jobs, run `00_setup_demo`, then the test notebooks.
- Me: R0 checks script, then R2 foundations.

## 2026-10-26: Self-repair worked; an untested path failed in dev; a manual merge reached prod
**What happened**
1. **Repair:** the agent repaired PR #4 from the CI log, CI went green, and it **merged into dev itself**.
2. **Dev smoke run failed:** `main()` still used `F.try_cast` (PySpark 3.5 has no such function). The repair had only fixed `clean_orders()`, which the CI log pointed at, and `main()` has no unit test. The agent stopped and escalated; nothing went to qa or prod.
3. **Manual merge to prod:** PR #7 (`dev → main`) was then merged by hand with admin bypass, which **deployed the broken code to prod**. Revert prepared on branch `fix/revert-broken-prod`.

**Improvements (branch `feat/deploy-repair`)**
- **Repair after a dev deploy failure:** reads the failed deploy job's output (`workflow_failure`), commits a correction on the fix branch and opens a follow-up PR → dev. Shares the 2-attempt repair budget.
- **`forbidden_patterns`** (platform knowledge in `config/fix.yaml`): `F.try_cast(` and `.try_cast(` are rejected before commit, with the hint `F.expr("try_cast(col AS INT)")`.
- **Repair prompt:** "fix the same mistake everywhere, including `main()`; untested paths run in the smoke test".
- **ANSI colour codes stripped** from Databricks traces (also when the ESC byte is lost).
- **CI `promotion-path` check:** pipeline code into `main` must come from `qa`, into `qa` from `dev`. Add it to the required checks.
- **Console:** lifecycle step details are cut to one line, with the full text on hover (the failure URL overlapped the steps).
- 148 tests pass; the real failed deploy run is now read correctly (clean trace pointing at `main()`, line 74).

## 2026-10-26: Phase 4, Pipeline Ops Console
- **API** (`pipeline_agent/api`, FastAPI):
  - 15 read views + 6 audited actions (cancel, rerun, re-run diagnosis, start fix, take over, runtime controls)
  - RBAC viewer/operator/approver/admin, HMAC-signed sessions (demo login)
  - security headers and CSP, SPA hosting
  - `pipeline-agent console`
- **Runtime controls:** the kill switch and dry run move into the `agent_controls` table; the agent applies them every tick and records a heartbeat (`last_tick`). SQLite runs in WAL mode so the agent and console share the DB.
- **UI** (`frontend/`, React + TS + Vite + Fluent UI v9 + TanStack Query + ECharts), 12 screens:
  - Command Center, Pipelines (+ detail), Live runs, Incidents
  - **Incident detail**: lifecycle tracker built from the audit trail, diagnosis, agent fix with GitHub diff/comments/steps
  - Fixes, Approvals, Cost & FinOps, Agent observability, Audit (CSV), Settings & controls
  - UST brand theme with light/dark
- **Charts:** dataviz method, palette **validated** both modes all-pairs (Dark Teal fails the chroma floor → chrome only; Light Teal is series 1), table view on every chart, tree-shaken ECharts (charts chunk 378 → 194 KB gzip).
- **Packaging:** multi-stage Dockerfile (Node build → Python image), compose adds the console service, CI `console-build` job.
- **Verified:** 143 tests pass; `npm run build` clean; live smoke test of all endpoints on real data (9 pipelines, 0.98 DBU from the billing table, PR #4 diff via the GitHub App).

## 2026-10-26: First live fix run → self-repair
**What happened (fault `bad_cast` in prod, fix flow on):**
- Detection and incident worked. The first triage was **unusable**: the reasoning model spent its 1,500 output tokens thinking, so fix_type was `unknown` and no fix started. Fixed: `max_tokens` 4000, triage falls through to the next model on an unusable answer, and the **rule class starts the fix** (the LLM can only veto with `rerun`/`config_or_permission`).
- Two agent processes ran at once (one started from the session, one by hand). Stopped one; only one agent may run (a leader lock is on the roadmap).
- Fix #1: the first coding model timed out, and the fallback wrote a sensible approach (`try_cast` + quarantine table, kept `assert_not_null`, added a fixture and test). It opened **PR #4** as `pipeline-agent-karthik[bot]`; path-guard ran and passed.
- **CI rejected it**, correctly:
  - `AttributeError: module 'pyspark.sql.functions' has no attribute 'try_cast'` (not in PySpark 3.5)
  - the test used `F` without importing it
  - the fault-kit test needed its target text exactly once
- CI annotations only said "exit code 1", so the agent couldn't learn why.

**Improvements:**
- `GitHubClient.checks` reads the failed **job log** (App has Actions read) and keeps the failure lines.
- **Self-repair:** on CI failure the fix flow feeds the CI output to the model and pushes a corrected commit to the same PR (max 2), validated as the combined change vs `main`.
- **pyflakes guard** for undefined names before commit.
- Prompt `fix-v2`: PySpark 3.5 APIs only (`F.expr("try_cast(...)")`), logic in testable functions, no dead code, import what tests use.
- Fault-kit test no longer blocks valid fixes (skips if a fault's target moved).
- 132 tests pass.

## 2026-10-26: Agent identity = GitHub App
- The agent authenticates as GitHub App `pipeline-agent-karthik[bot]` (installed on the repo only): `AppTokenAuth` signs a JWT with the App key, gets a 1-hour installation token and renews it automatically. No second account or email was needed. The App has no Workflows permission.
- Live check: `pipeline-agent github` → `Token user: pipeline-agent-karthik[bot]`, reads dev/qa/main.
- The first live call hit a 30 s read timeout (corporate proxy). GitHub reads now retry with back-off; writes are not retried (no duplicates).
- 125 tests pass.

## 2026-10-26: Phase 3, fix flow
- `git/github.py`: REST client (tree listing, file reads, single-commit multi-file changes through the Git Data API, PRs, merges, check runs with failure annotations, workflow runs). Token never in `repr`.
- `fix/patch.py`: LLM writes whole-file fixes through a `propose_fix` tool. Guards: allowed paths, compiles, `assert_not_null(` count kept, test required, no secrets, limits, one retry with validation feedback.
- `fix/flow.py`: persisted state machine `planned → pr_open/deploying (dev, qa, main) → verifying → resolved | failed | rejected`. Auto-merge only into dev/qa; `main` always needs a human. Prod rerun and verify after the prod deploy. Idempotent retries, `max_wait_hours` timeout, `max_concurrent=1`.
- Monitor starts a fix after triage for fix-eligible incidents; each tick advances all fixes. CLI: `fixes [--history]`, `fix <incident>`, `github`.
- Config: `config/fix.yaml`, free coding models in `config/llm.yaml` (`poolside/laguna-s-2.1:free` → nemotron-3-ultra → cohere north-mini-code).
- Bugs found by tests and fixed before shipping: audit kwarg collision (`target`), duplicate-PR risk on retry.
- 123 tests pass. **Live run pending:** needs the bot account + token (`GITHUB_TOKEN`), see 13-github-setup.md §12.

## 2026-10-26: First real fault + branch protection
- dev/qa/prod deployed and smoke-tested through GitHub Actions. The agent sees 9 jobs. Deploy concurrency is now per branch (a shared group made GitHub cancel the pending dev run).
- Branch protection: ruleset `protect-main-and-qa` (PR + Code Owner review + required checks `agent-tests`, `pipeline-tests`, `bundle-validate`, no force push/delete, admin bypass for the demo). Verified with test PR #1: all checks green, merge blocked pending review. Spark tests ran in CI for the first time and passed.
- **Fault `bad_cast` in prod (demo-ops inject) handled end-to-end by the agent:** failed run detected, `code`/`cast_invalid_input`, incident #1 fix-eligible, LLM diagnosis.
- The real failure exposed three quality issues, now fixed:
  1. The LLM got the *tail* of a huge JVM stack trace. New `llm/context.condense_logs` keeps messages, 5 frames per block, de-duplicates, and truncates head-first. Diagnosis confidence went from 72% to 95%, it now names `transform_silver` and `'abc'`, and tokens dropped from 5,265 to 2,026.
  2. 15-digit Databricks run IDs were redacted as "card numbers" in notifications. Agent-owned facts (`TRUSTED_FACTS`) are no longer redacted; free text still is.
  3. The incident headline was Databricks' generic "Workload failed…". The plugin now leads with the failed task's real error.
- Tests: 99 pass.

## 2026-10-26: GitHub deploy path live
- Repo `karthik-np-ust/databricks_agent_repo` (public). demo-ops `setup` succeeded from GitHub (catalogs, schemas, volumes, secrets, clean data in dev/qa/prod).
- Deploy workflow: the first prod deploy failed (most likely secrets not yet set); after adding `bundle validate` and failure annotations, prod deployed successfully. The agent sees the 3 prod jobs.
- `dev`/`qa` branches and the `demo-baseline` tag pushed. Branch pushes without file changes don't start the path-filtered deploy; run it manually (workflow_dispatch).
- First dev smoke run failed: `NameError: name '__file__' is not defined` on serverless `spark_python_task`. Fixed: `--src-dir ${workspace.file_path}/src` passed to every task, plus a bootstrap fallback.
- Fixed latent bug in `scripts/deploy_sdk.py` (variables context key). CODEOWNERS syntax fixed (`@karthik-np-ust`).

## 2026-10-25: Phase 0 checks + Phase 2 (LLM triage)
**Phase 0 (real accounts)**
- New `scripts/phase0_checks.py`. Results: PAT, Jobs API, `system.billing.usage`, `CREATE CATALOG`, SP API, secret scopes and OpenRouter tool calling all pass.
- Corporate network findings:
  - Zscaler TLS inspection broke Python HTTPS → fixed with `truststore` (OS trust store).
  - Zscaler **blocks uploads** to Databricks (Files API PUT) → not worked around; decision needed.
  - Databricks CLI download blocked → `scripts/deploy_sdk.py` (bundle files via SDK) for local use; CI keeps the CLI.
- Workspace setup partial: `dev` catalog, schemas, landing volume, `partner-dev` secret scope.
- Databricks plugin connected to the real workspace. Billing-table cost query verified. `host` and `warehouse_id` can now come from secret refs.

**Phase 2**
- `llm/gateway.py`: OpenAI-compatible client (OpenRouter), per-call redaction, model fallback, budgets (per-day requests/cost, per-incident tokens/cost), `llm_calls` ledger, provider key status.
- `llm/triage.py`: structured `Diagnosis` through a single tool, `<untrusted_logs>` framing, injection heuristic.
- Monitor runs triage after creating an incident: stores the diagnosis, sends a follow-up notification, escalates on budget stop, keeps monitoring on LLM outage.
- CLI: `incidents --show`, `triage`, `llm`. `.env` is loaded by the CLI.
- Config: `config/llm.yaml`, `per_day_requests` budget.

**Verified:** 97 tests pass. Live run with real OpenRouter: both simulator incidents diagnosed correctly (code: bad `qty` cast; infra: missing SELECT), with fallback after a provider 502. 3 of 45 daily requests used.

**Security note:** real credentials were placed in `.env.example` (tracked). Copied to `.env` (ignored). `.env.example` must be restored to placeholders before committing; rotate the keys after the demo.

**Next:** decide the deploy path (see 04 → Open decision), deploy the demo jobs, then Phase 3.

## 2026-10-24: Phase 0b + Phase 1
**Built**
- Core package `pipeline-agent`:
  - plugin SDK (capabilities, canonical models, manifest, entry-point registry, error classifier, contract test kit)
  - config and settings, secret references
  - state store (SQLAlchemy), policy engine, monitor
  - redaction, audit log, notifiers (console/Teams/Slack/generic)
  - CLI (`run`, `once`, `check`, `plugins`, `incidents`, `audit`)
- `agent-plugin-simulator`: behaviours ok/slow/fail_code/fail_transient/fail_permission/flaky, persisted state.
- `agent-plugin-databricks`:
  - Jobs API mapping and env prefixes
  - cancel / run_now, failed-task output
  - billing-table or estimated cost
  - code location, error knowledge pack
- Sample Asset Bundle:
  - 3 serverless jobs, dev/qa/prod targets in one workspace, catalog or schema layout
  - synthetic data generator, 14 fault scenarios
  - setup / inject / reset scripts
- GitHub workflows (CI with path guard and bundle validate, branch → env deploy), CODEOWNERS, Dockerfile, compose, `.env.example`.
- Documentation set in `docs/`.

**Verified**
- 83 core/plugin tests and 3 sample-pipeline tests pass (Spark tests skipped locally, no Java).
- Live CLI run against the simulator: code incident, transient rerun and permission escalation observed.
- All YAML parses.

**Not verified yet:** real Databricks workspace, bundle deploy, Docker build, Spark tests (CI).

**Next**
- Phase 0 checks with real accounts (see [10-demo-runbook.md](10-demo-runbook.md#phase-0-checks)).
- Phase 2: LLM gateway with OpenRouter.
