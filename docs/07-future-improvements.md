# 07 · Future improvements

## Next phases (from the approved plan)
| Phase | Deliverable |
|---|---|
| 2 | LLM gateway (OpenRouter now, approved provider later): token and cost meter per call, per-incident and per-day budgets, root-cause summary in incidents and notifications |
| 3 | Fix flow: LangGraph state machine (triage → diagnose → patch → dev → qa → human approval → prod → verify), sandboxed repo/log tools, GitHub client, `git blame` evidence |
| 4 | FastAPI (REST + SSE) and Ops Console MVP (Command Center, Pipelines, Live Runs, Incident detail with diff viewer, Cost & FinOps) |
| 5 | Hardening: prompt-injection tests, leader lock, verified Docker image, structured logging |
| 6 | Snowflake plugin + OpenLineage receiver, proving a second platform with zero core changes |

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
