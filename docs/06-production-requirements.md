# 06 · Requirements for production

What has to be true before the agent acts on real production pipelines. Items marked ✅ are already in place.

## Platform and licensing
- [ ] **Paid Databricks workspace(s).** Free Edition forbids commercial use and has no SLA.
- [ ] Separate workspaces (or at least separate catalogs with enforced grants) for dev, qa and prod.
- [ ] `system.billing.usage` and `system.billing.list_prices` readable by the agent's identity, with a SQL warehouse for cost queries.
- [ ] Jobs owned by, and run as, a service principal (`run_as`), not a person.

## Identity and access
- [ ] **Agent identity:** a workspace service principal (OAuth M2M, not a PAT) with:
  - *Can Manage Run* on monitored jobs
  - read on job run output
  - `SELECT` on `system.billing.*`
  - write on the dev catalog only
  - no prod data write
- [ ] **CI identity:** a separate service principal that can deploy bundles to each env. Its credentials live only in GitHub Environments (`dev`, `qa`, `prod`).
- [ ] **GitHub:** a GitHub App (preferred) or fine-grained PAT, scoped to the pipeline repo with contents + pull-requests write. It must not be able to bypass branch protection.
- [ ] **Branch protection** on `qa` and `main`: PR required, Code Owner review, status checks (`ci`) required, no force push.
- [ ] **`prod` GitHub Environment** with required reviewers.
- [ ] CODEOWNERS with real teams (placeholders today).
- [ ] Console users signed in through Entra ID (OIDC) with role mapping (Viewer / Operator / Approver / Admin).

## Secrets
- [x] ✅ Config holds only secret references (`env://`); plain values are rejected.
- [ ] Azure Key Vault resolver (`kv://`) with Managed Identity.
- [ ] Secret rotation runbook (PAT/OAuth secret, GitHub App key, LLM key).

## LLM
- [ ] UST-approved provider (Azure OpenAI / Bedrock / Anthropic) instead of OpenRouter. Data must stay in an approved region.
- [ ] LLM gateway (LiteLLM or API Management) with per-team budgets, request logging and model allowlist.
- [x] ✅ Redaction of secrets and personal data before anything leaves the agent.
- [ ] Prompt-injection test suite (scenario 12) passing in CI.

## Hosting (Stage 2 pilot)
- [ ] Azure Container Apps (or ECS Fargate), 1 always-on replica, image from a private registry.
- [ ] Azure Database for PostgreSQL with backups and point-in-time restore. Set `AGENT_DATABASE_URL`.
- [ ] HTTPS ingress for GitHub and Databricks webhooks (polling stays as a fallback).
- [ ] Logs and metrics to App Insights / Log Analytics. Alerts on agent health (poll lag, adapter errors).
- [ ] **Single active instance** (leader lock) until Stage 3 queue-based workers exist. Two pollers must not act on the same run.

## Operational readiness
- [x] ✅ Kill switch (`AGENT_ENABLED=false`) and dry run (`AGENT_DRY_RUN=true`).
- [x] ✅ Rerun caps, circuit breaker, notify-only for non-prod.
- [x] ✅ Append-only audit log.
- [ ] Start every new pipeline in **dry run for at least a week**, then review decisions before enabling actions.
- [ ] On-call runbook: what the agent does, how to stop it, how to take over an incident.
- [ ] Defined RTO/RPO for the agent's database.
- [ ] Data-retention policy for audit, incidents and LLM call logs.
- [ ] Change management sign-off: the agent changes production state (cancel/rerun) and proposes code changes.

## Quality gates
- [x] ✅ Core + plugin contract tests in CI.
- [ ] Databricks plugin contract tests against **recorded** responses from a real workspace (VCR).
- [ ] Fault-catalog scorecard (all 14 scenarios) passing end-to-end on the pilot workspace.
- [ ] Load test: 1000+ jobs per workspace; check poll duration and API rate limits.
