# 03 · Folder structure

The pipelines themselves live in a separate repo ([karthik-np-ust/orders-pipelines](https://github.com/karthik-np-ust/orders-pipelines), locally `C:\Users\285509\orders-pipelines`); this repo is only the agent.

```
databricks_agent/
├─ README.md                        quick start + doc index
├─ pyproject.toml                   core package "pipeline-agent" (CLI: pipeline-agent); deps incl. langgraph, cryptography
├─ Dockerfile, docker-compose.yml   one image; services dataplane / control / console / Postgres, secrets split per service
├─ .env.example                     every variable, marked with the process that uses it
│
├─ config/
│  ├─ platforms.yaml                platform instances (simulator, Databricks: env prefixes, Git folders, git_url)
│  ├─ pipelines.yaml                per-pipeline policy: thresholds, rerun caps, auto_fix, reconcile checks
│  ├─ policy/autonomy.yaml          autonomy matrix (L0–L5), limits; hashed into every signed request
│  ├─ agents/*.yaml                 agent manifests: monitoring, triage, recovery, testing, reconciliation, console
│  ├─ llm.yaml, budgets.yaml        model fallback lists / aliases, LLM budgets
│  └─ fix.yaml                      pipelines repo, promotion dev → qa → main, guards (allowed/forbidden/protected)
│
├─ src/pipeline_agent/              CORE (never imports a platform SDK)
│  ├─ cli.py                        dataplane | control | console | approvals | kedb | keys | check | incidents | triage | fix | llm | audit …
│  ├─ runtime.py                    builds the two planes (build_data_plane, build_control_plane), checkpointer, policy engine
│  ├─ config.py                     Settings (AGENT_* env) + AgentConfig (config/*.yaml)
│  │
│  ├─ control_plane/                CONTROL PLANE (no platform credentials)
│  │  ├─ orchestrator.py            the LangGraph incident graph (intake … close) + routing
│  │  ├─ codefix.py                 code-fix nodes: fix, tests, repair, PRs, merge, deploy, KEDB proposal
│  │  ├─ runner.py                  pipeline-agent control: lease, starts/resumes threads, timeouts, retries
│  │  ├─ agents/                    monitoring, triage, planner, testing, reconciliation
│  │  ├─ approvals.py, registry.py, deps.py, state.py
│  ├─ boundary/                     TRUST BOUNDARY: capabilities, ActionRequest (signed), queue, events
│  ├─ data_plane/                   DATA PLANE: executor, ledger, router, observer (events + snapshots), service
│  ├─ autonomy/engine.py            policy decision point (bundle, rules, decision)
│  ├─ security/                     signing.py (Ed25519), redact.py, audit.py
│  ├─ kedb.py, evidence.py, leases.py
│  ├─ llm/                          gateway (budgets, ledger, redaction), model_gateway (aliases), triage
│  ├─ fix/patch.py                  Recovery fix writer + guards (the promotion lives in control_plane/codefix.py)
│  ├─ git/github.py                 GitHub REST client, GitHub App auth
│  ├─ api/                          Ops Console API: views (read), actions (queue, never execute), control (approvals, requests, KEDB, agents, policy, evidence)
│  ├─ sdk/                          plugin SDK: models, capabilities (incl. ActionProvider), plugin, registry, classify
│  ├─ knowledge/generic_errors.yaml platform-neutral KEDB seed
│  ├─ notify/, store/db.py, secrets.py, net.py, testing/contract.py
│
├─ plugins/
│  ├─ agent-plugin-databricks/      Jobs API 2.2, repair_run, Git-folder sync, git_source tests, SQL profiles, billing
│  └─ agent-plugin-simulator/       deterministic fake platform (incl. all data-plane actions) for demos and tests
│
├─ frontend/                        React + Fluent UI Ops Console (pages incl. Approvals, Requests & ledger, Known errors)
├─ scripts/                         phase0_checks.py (--redesign), inject_fault.py, _common.py
├─ tests/                           agent tests (see 12-testing.md)
├─ docs/                            this documentation
└─ var/                             (git-ignored) signing keys, evidence blobs, LangGraph checkpoints
```

## Why plugins are separate packages
A new ETL tool is a new package that registers an entry point (`pipeline_agent.plugins`) and passes the contract kit. The core, the control plane and the console don't change. Data-plane actions (repair, code sync, tests, profiles) are an optional `ActionProvider` on the same plugin; see [09](09-plugin-guide.md).
