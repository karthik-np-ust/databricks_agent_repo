# 03 · Folder structure

Status: ✅ built, ⏳ planned (folder doesn't exist yet).

```
databricks_agent/
├─ README.md                      quick start + doc index
├─ pyproject.toml                 core package "pipeline-agent" (CLI entry point: pipeline-agent)
├─ Dockerfile, docker-compose.yml container image; Stage 1 stack (agent + Postgres)
├─ .env.example                   every environment variable, with comments
├─ .gitignore                     excludes .env, local DBs, simulator state
│
├─ src/pipeline_agent/            ✅ CORE: never imports a platform SDK
│  ├─ __init__.py                 version + PLUGIN_API_VERSION
│  ├─ cli.py                      run | once | check | plugins | incidents | audit
│  ├─ runtime.py                  builds plugins, store, notifier, monitor from config
│  ├─ config.py                   Settings (AGENT_* env) + AgentConfig (config/*.yaml)
│  ├─ secrets.py                  env:// secret references (kv:// planned)
│  ├─ monitor.py                  the poll loop (tick), action execution
│  ├─ policy.py                   decision table, thresholds, caps, circuit breaker
│  ├─ sdk/                        plugin SDK
│  │  ├─ models.py                canonical data model
│  │  ├─ capabilities.py          Capability enum + RunSource/RunController/... protocols
│  │  ├─ plugin.py                PluginManifest, PlatformInstance, PlatformPlugin
│  │  ├─ registry.py              entry-point discovery, API version check
│  │  └─ classify.py              rule-based ErrorClassifier
│  ├─ knowledge/generic_errors.yaml   platform-neutral error rules
│  ├─ security/                   redact.py (masking), audit.py (append-only audit)
│  ├─ notify/webhook.py           console / Teams / Slack / generic notifiers
│  ├─ store/db.py                 SQLAlchemy models + StateStore
│  ├─ testing/contract.py         PluginContractSuite (plugin conformance tests)
│  ├─ net.py                      OS trust store (corporate TLS inspection)
│  ├─ llm/                        ✅ gateway.py (budgets, redaction, fallback, ledger), triage.py (diagnosis)
│  ├─ fix/                        ✅ patch.py (LLM fix + guards), flow.py (dev → qa → human → prod state machine)
│  ├─ git/                        ✅ github.py (REST: tree, commits, PRs, merges, checks, workflow runs)
│  ├─ llm/context.py              ✅ condenses JVM traces for LLM and humans
│  ├─ integrations/               ⏳ Phase 6: OpenLineage receiver, generic REST/SQL plugins
│  └─ api/                        ⏳ Phase 4: FastAPI for the Ops Console
│
├─ plugins/
│  ├─ agent-plugin-simulator/     ✅ reference plugin: fake pipelines with behaviours
│  │  ├─ src/pipeline_agent_simulator/plugin.py, knowledge/errors.yaml
│  │  └─ tests/                   contract suite + simulator tests
│  ├─ agent-plugin-databricks/    ✅ Databricks Jobs plugin
│  │  ├─ src/pipeline_agent_databricks/plugin.py, knowledge/errors.yaml
│  │  └─ tests/                   contract suite against a fake WorkspaceClient
│  └─ agent-plugin-snowflake/     ⏳ Phase 6
│
├─ config/                        ✅ agent configuration (no secrets)
│  ├─ platforms.yaml              platform instances (simulator + dbx-free)
│  ├─ llm.yaml                    model routing (triage and fix models, fallbacks)
│  ├─ fix.yaml                    fix flow: repo, promotion order, allowed paths, guards
│  ├─ pipelines.yaml              policies: thresholds, rerun caps, auto_fix, envs
│  └─ budgets.yaml                LLM budgets (per incident / per day)
│
├─ sample_pipelines/              ✅ demo Databricks Asset Bundle (what the agent watches and fixes)
│  ├─ databricks.yml              targets dev / qa / prod in one workspace
│  ├─ resources/jobs.yml          orders_pipeline (3 tasks), sales_report, partner_feed_ingest
│  ├─ src/                        ingest_bronze, transform_silver, build_gold, sales_report, partner_feed
│  │  └─ common/                  config (table names), schemas, dq checks
│  ├─ tests/ + fixtures/          pytest (pure Python + Spark; Spark needs Java)
│  ├─ faults/*.yaml               release-fault definitions (find/replace edits)
│  └─ requirements-dev.txt        pyspark + pytest for CI
│
├─ scripts/                       ✅ demo tooling (Databricks SDK)
│  ├─ _common.py                  client, SQL helper, job lookup, landing upload
│  ├─ generate_data.py            synthetic orders + data-fault scenarios
│  ├─ setup_workspace.py          catalogs/schemas/volume/secret scopes + clean data
│  ├─ inject_fault.py             runs any of the 14 fault scenarios
│  ├─ reset_demo.py               restores data, secrets, tables (optional git reset)
│  ├─ phase0_checks.py            feasibility checks (Databricks + OpenRouter)
│  └─ deploy_sdk.py               bundle deploy via Python SDK (when the CLI is unavailable)
│
├─ tests/                         ✅ core tests: monitor/policy scenarios, LLM gateway/triage, units, demo kit
├─ .github/
│  ├─ workflows/ci.yml            PR checks: tests, Spark tests, agent path guard, bundle validate
│  ├─ workflows/deploy.yml        branch → target deploy (dev/qa/main→prod) + smoke runs
│  ├─ workflows/demo-ops.yml      manual: workspace setup, fault injection, reset (runs on GitHub, not the laptop)
│  └─ CODEOWNERS                  approvers; admins own .github, config, scripts, faults
├─ frontend/                      ⏳ Phase 4: React + TypeScript + Fluent UI Ops Console
└─ docs/                          this documentation
```

## Why plugins are separate packages
Each plugin has its own `pyproject.toml` and registers itself through the `pipeline_agent.plugins` entry point. Installing it makes it available, and removing it leaves the core untouched. A new ETL tool is a new folder under `plugins/`; see [09-plugin-guide.md](09-plugin-guide.md).
