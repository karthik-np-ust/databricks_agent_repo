# Pipeline Production Support Agent

An agent that watches data pipelines and handles routine production-support work:

- reruns pipelines that run past their time threshold
- reruns temporary failures once
- opens incidents for code/data bugs (later: diagnoses and fixes them through a human-approved PR)
- escalates permission and config problems to a human, without retrying in a loop
- tracks compute cost and its own LLM spend

Databricks is the first platform. Snowflake and other ETL tools plug in through the same plugin SDK, with no changes to the core.

> **Status (2026-10-25):** Phases 0, 0b, 1 and 2 are done:
> - feasibility checks pass
> - sample jobs and fault kit built
> - plugin SDK, monitor and policy engine built
> - LLM triage running through OpenRouter, with budgets
>
> The agent is connected to the real Databricks workspace. Demo-job deployment is blocked by the corporate proxy (uploads). Next: the fix-PR flow (Phase 3) and the Ops Console (Phase 4). See [docs/04-scope-and-status.md](docs/04-scope-and-status.md).

## Quick start (no credentials needed)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]" -e plugins/agent-plugin-simulator -e plugins/agent-plugin-databricks

.\.venv\Scripts\pipeline-agent plugins     # installed plugins and their capabilities
.\.venv\Scripts\pipeline-agent check       # validate config, test connections
.\.venv\Scripts\pipeline-agent once        # one poll; wait ~1 min and run again to see decisions
.\.venv\Scripts\pipeline-agent incidents
.\.venv\Scripts\pipeline-agent audit
.\.venv\Scripts\pipeline-agent incidents --show 1   # incident + LLM diagnosis
.\.venv\Scripts\pipeline-agent llm                   # LLM budgets and spend (AGENT_LLM_ENABLED=true)
.\.venv\Scripts\pipeline-agent run         # poll continuously

.\.venv\Scripts\pytest                     # agent + plugin tests
```

The default config ([config/platforms.yaml](config/platforms.yaml)) points at the **simulator**. It has five fake pipelines that are slow, broken, flaky, missing a permission, or healthy. The second `once` shows the agent opening a code incident, rerunning the transient failure and escalating the permission failure. After 5 minutes it cancels and reruns the slow report.

To connect Databricks Free Edition, follow [docs/10-demo-runbook.md](docs/10-demo-runbook.md).

## Documentation

| Doc | What's in it |
|---|---|
| [01 Overview](docs/01-overview.md) | Goals, what the agent does, how a failure flows |
| [02 Architecture](docs/02-architecture.md) | Components, decision rules, data model, planned flows |
| [03 Folder structure](docs/03-folder-structure.md) | What every folder and file is for |
| [04 Scope and status](docs/04-scope-and-status.md) | Done / not done per phase, deviations from the plan |
| [05 Environment restrictions](docs/05-environment-restrictions.md) | Databricks Free Edition, OpenRouter, GitHub and local limits |
| [06 Production requirements](docs/06-production-requirements.md) | What's needed before real production use |
| [07 Future improvements](docs/07-future-improvements.md) | Roadmap beyond the current build |
| [08 Configuration](docs/08-configuration.md) | Settings, platforms, policies, budgets |
| [09 Plugin guide](docs/09-plugin-guide.md) | How to add a new ETL tool |
| [10 Demo runbook](docs/10-demo-runbook.md) | Phase 0 checks, workspace setup, running the fault scenarios |
| [11 Security](docs/11-security.md) | Controls in place and planned |
| [12 Testing](docs/12-testing.md) | Test suites and how to run them |
| [13 GitHub setup](docs/13-github-setup.md) | Repo, secrets, environments, branch protection, first deploy |
| [WORKLOG](docs/WORKLOG.md) | Change log of what was built, when |

<!-- branch protection test: safe to close this PR without merging -->
