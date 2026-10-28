# 05 · Current environment restrictions

Sources: [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations), [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits). Checked 2026-10-23.

## Databricks Free Edition
| Restriction | Impact on this project | How we handle it |
|---|---|---|
| Serverless compute only, limited size | No custom clusters | Notebook tasks on serverless (no cluster spec); test runs too |
| **Max 5 concurrent job tasks per account** | Three envs × three jobs, plus agent test runs, could exceed it | Tasks run sequentially; only prod is scheduled. The data plane executes one request at a time, and test notebooks of a fix run as a sequential chain (`run_if: ALL_DONE`). |
| One active pipeline per type (Lakeflow) | No Lakeflow Declarative Pipelines per env | Demo uses Jobs, not DLT |
| **Fair-use overrun shuts compute down for the rest of the day** | Rerun loops could kill the demo | Rerun cap (2/day/pipeline), circuit breaker (10 actions/hour), dry-run mode |
| **One workspace, one metastore** | No workspace-level dev/qa/prod isolation | Name prefixes, separate bundle root paths, separate catalogs (or schemas) |
| Outbound internet limited to trusted domains | The agent can't run inside Databricks and call OpenRouter/GitHub | The agent runs outside Databricks |
| Databricks Apps stop after 24h; max 3 apps | Not suitable for hosting the agent | Agent hosted locally (Stage 1) or in Azure (Stage 2) |
| **`__file__` is not defined in serverless `spark_python_task`** (found 2026-10-26) | Scripts couldn't locate `common/` | Gone with the redesign: pipelines are notebooks of top-level cells with widgets (no imports between files) |
| Git folders in `/Workspace/Shared`, reported as `DIRECTORY` by `get_status` and missing from `repos.list` | Writable by all workspace users; the Repos API can't list them | The adapter finds the folder by path and uses `repos.get/update(object_id)` (R0 check). Production: per-env folders owned by a service principal |
| No account console / account APIs, no SSO/SCIM | No account-level service principals or SCIM groups | PAT for a bot user. Workspace SPs to be checked in Phase 0. |
| `system.billing.usage` availability **not documented** | Real DBU cost may be unavailable | `use_billing_table: false` by default → duration-based estimate. Phase 0 checks it. |
| CREATE CATALOG **not documented** | Catalog-per-env may fail | `layout: schema` fallback (`workspace.<env>_bronze`) in code, bundle and scripts |
| Single user owns everything | Can't demo a revoked grant | Scenario 11 uses a deleted secret instead |
| **No commercial use** | Demo/POC only | Production needs a paid workspace ([06-production-requirements.md](06-production-requirements.md)) |
| No SLA, inactive accounts may be deleted | Demo could disappear | The pipelines repo's `setup/00_setup_demo` notebook and `jobs/*.json` rebuild everything ([15](15-databricks-setup.md)) |

## OpenRouter (Phase 2)
| Restriction | Impact | Plan |
|---|---|---|
| `:free` models: 20 req/min, **50 req/day** below $10 lifetime credit | About 1–3 incidents/day (15–40 calls each) | Buy $10 credit → 1000 free req/day. Set a key spend limit. |
| Free model availability and quality vary | Fix quality may vary | Model IDs in config: free model for triage, paid coding model for fixes |
| Third-party proxy | Not acceptable for enterprise data | Demo only. Stage 2 uses a UST-approved provider behind a LiteLLM gateway. |

## GitHub
| Restriction | Impact | Plan |
|---|---|---|
| Branch protection and Environment reviewers on **private** repos need GitHub Team/Enterprise | The approval gate wouldn't be enforced on a free private repo | Use UST GitHub Enterprise, or a **public** demo repo with no sensitive code |
| CODEOWNERS uses placeholder teams | Reviews not enforced until replaced | Replace `@your-org/...` before the demo |
| `vars.AGENT_BOT_LOGIN` must be set | Path guard is skipped otherwise | Set the repo variable to the bot account's login |

## Corporate network (UST laptop, Zscaler), found 2026-10-25
| Restriction | Symptom | How we handle it |
|---|---|---|
| **TLS inspection** (corporate root CA) | `requests` fails `CERTIFICATE_VERIFY_FAILED`; databricks-sdk retries silently for 5 minutes ("Failed to fetch host metadata … Timed out") | `truststore` (core dependency) makes Python trust the Windows certificate store. Called by the CLI ([net.py](../src/pipeline_agent/net.py)) and the scripts. |
| **Uploads to Databricks blocked** | `PUT /api/2.0/fs/files/...` → `PermissionDenied: Internet Security by Zscaler` | Not worked around (corporate control). Seed data and deploy from GitHub Actions, or get an IT allowlist. JSON API calls are not affected. |
| **Executable downloads blocked** | Databricks CLI zip from GitHub: connection reset after ~300 KB, then 403 | No longer needed: the pipelines repo is checked out as Git folders and seeded by notebooks inside Databricks (was: `scripts/deploy_sdk.py` deploys the bundle definition with the Python SDK. CI installs the CLI normally. |
| Legacy CLI on PATH | `C:\Python313\Scripts\databricks.exe` is the old 0.18 CLI (no bundles) | Don't use it for bundles |

## OpenRouter (observed 2026-10-25)
- The key is **free tier**: 50 free-model requests per day. The `/key` counter can lag behind real usage.
- Free providers return occasional **502** errors. The gateway falls back to the next model in `config/llm.yaml`.
- Free-model latency is about 5–10 s per triage call.

## This development machine
| Restriction | Impact |
|---|---|
| No Docker | Dockerfile and compose not built or tested here. SQLite used locally. |
| No Java | Spark tests for the sample pipelines skip locally; they run in CI |

## Redesign-specific limits (2026-10-28)
| Restriction | Impact | How we handle it |
|---|---|---|
| OpenRouter free tier: ~45 requests/day | Several LLM agents per incident would exhaust it | KEDB-first triage (transient/infra matches use no LLM); a code fix uses about 2–5 requests (diagnosis, fix, tests, repairs); budgets stop and escalate |
| No Docker on this laptop | `docker compose` (split secrets, PostgresSaver checkpoints) is not run here | Verified locally with SQLite and three plain processes; compose is validated as YAML only. Build and run it in CI or on the pilot host |
| No Java/PySpark locally | Pipeline notebooks can't run locally | By design: tests run in Databricks (Testing agent + `tests.run`); the pipelines repo's CI lints notebooks |
