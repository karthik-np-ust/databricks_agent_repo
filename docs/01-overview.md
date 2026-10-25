# 01 · Overview

## Problem
Production support for data pipelines is mostly repetitive work:
- spotting runs that hang or run far too long, then cancelling and rerunning them
- rerunning after a network blip
- reading stack traces to tell a real bug from a permissions problem
- writing a fix, testing it in dev and QA, getting it approved and deployed, then rerunning prod

The agent automates the routine parts and leaves the judgement calls to people. Every action is logged.

## What the agent does

| Situation | Agent behaviour | Status |
|---|---|---|
| Run exceeds its time threshold (prod) | Cancel and rerun, at most 2 times per pipeline per day. After that, escalate to a human. | ✅ built |
| Temporary failure (timeout, HTTP 503, connection reset) | Rerun once. If it fails again, escalate. | ✅ built |
| Code or data bug (bad cast, divide by zero, schema drift, …) | Open an incident marked fix-eligible | ✅ built |
| Same, with auto-fix on | Diagnose with an LLM, fix on a branch, promote dev → qa, human approves the PR to `main`, CI deploys prod, rerun prod | ⏳ Phases 2–3 |
| Permission, secret or quota problem | Escalate only: no code change, no rerun loop | ✅ built |
| Unclassified failure | Escalate | ✅ built |
| Anything in dev or qa | Notify only (configurable per pipeline) | ✅ built |
| Compute cost | Per-run DBU from the billing table or an estimate | ✅ plugin side; dashboard in Phase 4 |
| Own LLM spend | Per-incident and per-day budgets | ⏳ Phase 2 |

## How a failure flows today

```
platform run ──poll──► Monitor ──► classify error (rules) ──► Policy engine ──► action
                                   │                                             ├─ cancel + rerun / rerun
                                   │ secrets and personal data are               ├─ open incident (code)
                                   │ redacted before storage or notification     ├─ escalate (infra/unknown)
                                   ▼                                             └─ notify only (dev/qa)
                            state store + audit log  ──────────────────────────► Teams / Slack / console
```

## Principles
- **Human approval for prod changes.** The agent never holds prod deploy credentials. Prod changes only arrive through an approved PR merged to `main` and deployed by CI.
- **Platform-neutral core.** Each ETL tool is a plugin. The core never imports a platform SDK.
- **Safe by default.** There is a kill switch and a dry-run mode. Rerun caps, a circuit breaker and notify-only mode for non-prod environments limit what the agent can do.
- **Untrusted input.** Logs and data are treated as untrusted. They are redacted before anything is stored, notified or sent to an LLM.
- **Idempotent.** Restarts and overlapping polls never act twice on the same run.
