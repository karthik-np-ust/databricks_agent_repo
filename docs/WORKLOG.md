# Work log

Newest first. Each entry: what was built, how it was verified, what's next.

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
