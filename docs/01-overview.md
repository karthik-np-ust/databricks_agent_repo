# 01 · Overview

## Problem
Production support for data pipelines is mostly repetitive work:
- spotting runs that hang or run far too long, then cancelling and rerunning them
- rerunning after a network blip
- reading stack traces to tell a real bug from a permissions problem
- writing a fix, testing it in dev and QA, getting it approved and deployed, then rerunning prod

The agent automates the routine parts and leaves the judgement calls to people. Every action is policy-checked, signed and logged.

## What the agent does

| Situation | Behaviour | Autonomy |
|---|---|---|
| Temporary failure in prod (timeout, HTTP 503, connection reset) | KEDB match → rerun → watch the run → reconcile (run state + data checks) → resolved; a second failure escalates | L4 (within rerun caps) |
| Run exceeds its time threshold | Asks for approval, then cancels and reruns | L3 (console) |
| Code or data bug, `auto_fix` on | Diagnose → Recovery writes the fix → **Testing agent tests it in Databricks** → dev → qa → your GitHub merge → prod Git folder synced → failed run repaired → reconciled; a KEDB entry is proposed | L4 dev/qa, L3 prod (GitHub merge) |
| Code or data bug, `auto_fix` off | Diagnosed and parked; an approver clicks **Start fix** | – |
| Permission, secret or quota problem; unclassified | Escalate with the evidence; no rerun loop, no code change | L1 |
| dev or qa failures | Recommend only | L2 |
| Console cancel / rerun | Executed as your signed request | L3 (your session) |
| Compute cost and LLM spend | Billing table (or estimate) per run; LLM budgets per incident and per day | – |

## How a failure flows

```
Databricks ──► DATA PLANE observer ──events + redacted evidence──► CONTROL PLANE (LangGraph, one thread per incident)
                                                                     Monitoring → Triage (KEDB first, LLM if needed)
                                                                     → Planner → Policy (L0–L5) ─┬─ L3: approval (console / GitHub merge)
                                                                                                 └─ signed ActionRequest
Databricks ◄── DATA PLANE executor ◄── verify signature, policy, live preconditions, ledger ◄─────┘
                    └── run outcome / test results ──► Reconciliation ──► resolved / escalated ──► Teams / Slack / console
```

Details: [02 · Architecture](02-architecture.md).

## Principles
- **Agents request, they never execute.** Only the data plane changes a platform, and only on a signed request that passes policy twice and the live preconditions, and it executes each request exactly once (ledger).
- **Human approval where it matters.** L3 actions need an approver. Prod code needs your merge in GitHub, and that merge is the only approval the prod sync and repair accept.
- **Credentials stay with their owner.** The platform token lives only in the data plane; the control plane and the console never hold it.
- **Platform-neutral core.** Each ETL tool is a plugin. The core never imports a platform SDK.
- **Safe by default.** Kill switch, dry run, rerun caps, circuit breaker, LLM budgets, and recommend-only for non-prod.
- **Untrusted input.** Logs and data are redacted before anything is stored, notified or sent to an LLM, and reach a model only framed as untrusted evidence.
- **Curated knowledge.** The Known Error DB is checked before any LLM call; agents only propose new entries.
