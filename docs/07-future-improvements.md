# 07 · Future improvements

## After the redesign (R0–R7 built)
| Item | Why | Notes |
|---|---|---|
| **Read-only diagnostic MCP servers** | The reference's box 8: Triage could ask follow-up questions (schemas, last good parameters) through a standard protocol | Deliberately not built: the observer pushes the evidence, and each tool call costs an LLM request. The tool gateway is an internal interface, so an MCP client can be added |
| OPA instead of the in-process evaluator | Policy as a CNCF-graduated service, shared with other systems | The decision is already OPA-shaped (input → `{allow, level, …}`); port `autonomy.yaml` to Rego |
| pgvector runbook search, graph store | Similar-incident retrieval, dependency reasoning | KEDB is regex and priority today |
| Langfuse / Phoenix, OpenTelemetry | Agent-quality and trace views | `llm_calls`, the audit log and the ledger cover the demo |
| LiteLLM-class model gateway | Central keys, routing, caching | `ModelGateway` aliases make it a config change |
| Event bus (Service Bus / Kafka) | Push instead of polling the `events` table | `EventLog` is the seam |
| Workload identity, Key Vault (`kv://`) | No long-lived tokens | `env://` today; the identity broker is pilot work |
| Entra ID OIDC for the console | Real authentication | Demo login today |
| Snowflake adapter (phase 2 of the reference) | Second platform on the same capability contract | `RunSource` + `RunController` + `LogProvider` + `ActionProvider` (`EXECUTE TASK … RETRY LAST` for repair) |
| Teams approval cards | Approve L3 actions where people already are | Approvals API exists; add an adaptive-card action |

## Hosting roadmap
| Stage | Hosting | Changes |
|---|---|---|
| 1 Demo | Laptop, `docker compose` (or plain venv) | Polling only; nothing exposed |
| 2 Pilot | Azure Container Apps + PostgreSQL + Key Vault + Managed Identity | Webhooks in, Entra SSO, Teams cards, approved LLM provider |
| 3 Enterprise | AKS/Container Apps, split into services: ingestor → Service Bus → per-platform workers (KEDA) → workflow engine → LLM gateway | Event-driven, multi-tenant, ServiceNow/Jira sync, OpenTelemetry, DR |

## Agent capabilities
- **Smarter thresholds:** separate baselines by weekday/hour, data volume and input size. Use anomaly detection instead of a fixed p95 × multiplier.
- **Repair instead of rerun:** Databricks `runs/repair` reruns only the failed tasks, which saves compute.
- **Upstream awareness:** when an upstream job failed, don't open a duplicate incident downstream; link them through lineage (Unity Catalog lineage / OpenLineage).
- **Incident grouping:** group repeated failures of the same root cause into one incident with a counter.
- **Learning from approvals:** keep accepted and rejected fixes as few-shot examples and as new knowledge-pack rules.
- **Proactive checks:** schema-drift detection on landing files before the job runs; freshness SLAs (data not updated by time X).
- **Cost actions:** recommend right-sizing (serverless performance mode, cluster policies) and flag runaway queries.
- **Auto-tuned rerun policy:** back-off between reruns, and no reruns during known platform incidents (status page).

## Platforms
Planned plugin order: Simulator ✅ → Databricks ✅ → Snowflake → dbt → Airflow → ADF/Fabric → AWS Glue → Informatica / Matillion / Fivetran (generic REST plugin).

Low-code onboarding:
- **OpenLineage receiver:** monitoring without plugin code.
- **Generic REST plugin:** YAML maps endpoints and JSONPath fields.
- **Generic SQL-history plugin:** a query template over task/query history.

## Developer experience
- `pipeline-agent new-plugin <name>` cookiecutter.
- Recorded-response (VCR) fixtures for plugin contract tests.
- Pre-commit hooks (ruff, mypy) and type-checking in CI.
- Alembic migrations once the schema settles (today: `create_all`).

## Ops Console (Phase 4+)
- Approvals inbox, posting reviews with the approver's own GitHub OAuth.
- Onboarding wizard generated from each plugin's config JSON-Schema.
- Teams tab app and Adaptive Card actions (acknowledge, take over, pause agent).
- SLA reporting, MTTR and auto-resolution rate trends.
