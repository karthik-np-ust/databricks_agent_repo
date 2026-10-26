# Work log

Newest first. Each entry: what was built, how it was verified, what's next.

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
