# Pipeline Production Support Agent

A multi-agent AIOps system for data pipelines. It follows the **Agentic AIOps for DataOps v2** reference architecture: LangGraph agents in a control plane decide; a credential-holding data plane executes only signed, policy-checked requests.

What it does:

- **Detects** failed and over-threshold runs (data-plane observer).
- **Triages** them: Known Error DB first, then an LLM diagnosis when needed, within budgets.
- **Recovers runtime problems:**
  - reruns transient failures in prod automatically (L4);
  - asks a human before cancelling a long run (L3).
- **Fixes code:**
  - the Recovery agent writes a notebook patch;
  - the Testing agent writes test notebooks that run **in Databricks** on the fix branch;
  - the fix is promoted dev → qa automatically, and prod waits for your GitHub merge;
  - the failed prod run is repaired.
- **Reconciles** outcomes: run state plus row, duplicate-key and null checks.
- **Escalates** infra and permission problems with the evidence, without retrying in a loop.
- **Records everything:** an audit log, an execution ledger, LLM spend, and a curated Known Error DB.

Pipelines live in their own repo ([karthik-np-ust/orders-pipelines](https://github.com/karthik-np-ust/orders-pipelines)), checked out in Databricks as one Git folder per environment. Databricks is the first platform; others plug in through the plugin SDK.

> **Status (2026-10-28):** redesign phases R0–R7 are built. See [docs/04-scope-and-status.md](docs/04-scope-and-status.md) and the [WORKLOG](docs/WORKLOG.md).

## Quick start (simulator, no credentials)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev,console]" -e plugins/agent-plugin-simulator -e plugins/agent-plugin-databricks
.\.venv\Scripts\pipeline-agent keys generate --out var/keys    # signing keys for the trust boundary
.\.venv\Scripts\pipeline-agent kedb seed                         # Known Error DB from the knowledge packs

# two processes (two terminals), plus the console
.\.venv\Scripts\pipeline-agent dataplane      # observe platforms, execute signed requests
.\.venv\Scripts\pipeline-agent control        # LangGraph incident graphs
.\.venv\Scripts\pipeline-agent console        # http://127.0.0.1:8080  (build the UI first: cd frontend; npm install; npm run build)

.\.venv\Scripts\pipeline-agent approvals      # L3 actions waiting for you (or use the console)
.\.venv\Scripts\pipeline-agent incidents
.\.venv\Scripts\pytest                        # all tests, no network
```

To try only the simulator, disable `dbx-free` in [config/platforms.yaml](config/platforms.yaml). Within a minute or two you'll see:
- a transient failure rerun and resolved;
- a permission failure escalated;
- a code failure parked for a fix;
- after 5 minutes, the slow report waiting for your approval.

To connect Databricks Free Edition and the pipelines repo, see [docs/15-databricks-setup.md](docs/15-databricks-setup.md), then [docs/10-demo-runbook.md](docs/10-demo-runbook.md).

**Docker:** `docker compose up` runs `dataplane`, `control`, `console` and Postgres. Each service gets only its own secrets; see [docker-compose.yml](docker-compose.yml) and [docs/08-configuration.md](docs/08-configuration.md).

## Documentation

| Doc | What's in it |
|---|---|
| [01 Overview](docs/01-overview.md) | Goals and how a failure flows |
| [02 Architecture](docs/02-architecture.md) | Reference architecture → implementation, the incident graph, autonomy levels, trust boundary |
| [03 Folder structure](docs/03-folder-structure.md) | What every folder and file is for |
| [04 Scope and status](docs/04-scope-and-status.md) | Done / not done per phase, decisions and deviations |
| [05 Environment restrictions](docs/05-environment-restrictions.md) | Databricks Free Edition, OpenRouter, GitHub and local limits |
| [06 Production requirements](docs/06-production-requirements.md) | What's needed before real production use |
| [07 Future improvements](docs/07-future-improvements.md) | Roadmap beyond the current build |
| [08 Configuration](docs/08-configuration.md) | Settings per process, platforms, policies, autonomy, agents, budgets |
| [09 Plugin guide](docs/09-plugin-guide.md) | How to add a new ETL tool (including data-plane actions) |
| [10 Demo runbook](docs/10-demo-runbook.md) | Running both planes, fault scenarios, live demo order |
| [11 Security](docs/11-security.md) | Controls in place and planned |
| [12 Testing](docs/12-testing.md) | Test suites and how to run them |
| [13 GitHub setup](docs/13-github-setup.md) | GitHub App, branch protection |
| [14 Ops Console](docs/14-console.md) | Screens, roles, approvals, security, hosting |
| [15 Databricks setup](docs/15-databricks-setup.md) | Pipelines repo, Git folders, jobs |
| [WORKLOG](docs/WORKLOG.md) | Change log of what was built, when |
