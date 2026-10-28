# 14 · Pipeline Ops Console

A web console to watch and steer the agent. It's one React app served by a small API inside the agent package. It is part of the **control plane**:
- it reads the shared database;
- it **never calls a platform**: platform state comes from the data plane's snapshots;
- it **never executes** anything: actions become events, which the control plane turns into signed, policy-checked requests.

```
browser ──► pipeline-agent console (FastAPI, :8080)        no platform credentials, no signing keys
               ├─ /api/v1/*      read views + role-checked actions (queue only) + approvals
               ├─ /              built React app (frontend/dist)
               ├─ shared DB      incidents, work items, approvals, signed requests, ledger, KEDB, audit, LLM ledger,
               │                 platform snapshots (pipelines, active runs, health, costs) written by the observer
               ├─ evidence       redacted blobs (read-only)
               └─ GitHub (App)   PR diffs and comments for agent fixes
pipeline-agent control   ──► turns console events into graph threads; resumes them on approvals
pipeline-agent dataplane ──► executes signed requests; refreshes the snapshots every poll (costs every 30 min)
```

## Run it
```powershell
cd frontend; npm install; npm run build; cd ..      # once (npm install is slow behind the proxy)
.\.venv\Scripts\pipeline-agent console               # http://127.0.0.1:8080   API docs: /api/docs
.\.venv\Scripts\pipeline-agent dataplane              # other terminals: the two planes
.\.venv\Scripts\pipeline-agent control
```
For UI development, use `npm run dev` (http://localhost:5173, hot reload; it proxies `/api` to :8080).

## Screens
| Screen | What it shows | Actions (role) |
|---|---|---|
| **Command Center** | Agent status (active / dry run / stopped / not polling), KPIs, "approval needed" banners, 14-day runs chart, platforms, recent incidents, activity feed | – |
| **Pipelines** | Every pipeline across platforms and envs: last run, 14-day success rate, average duration, threshold, whether the agent acts or only notifies, auto-fix, open incidents | – |
| Pipeline detail | Run-duration chart with threshold line, policy, incidents | Rerun (operator): queued as an L3 request, executed by the data plane |
| **Live runs** | Active runs, elapsed vs threshold (warning at 80%, red over threshold) | Cancel (operator): queued as an L3 request |
| **Incidents** | All incidents with class, LLM summary, fix state, tokens | – |
| **Incident detail** | **Lifecycle tracker**, redacted error, LLM diagnosis, **Agents on this incident** (graph state and what it waits for; Databricks test runs; signed requests with level, approval, result and ledger state; evidence viewer; approvals), **agent fix** (PR links, diff, PR comments, history), LLM calls, audit trail | Re-run diagnosis (operator), Start fix (approver, code failures), Take over (operator: stops the graph, rejects its approvals) |
| **Agent fixes** | Every fix and where it is in the promotion | – |
| **Approvals** | L3 runtime actions waiting for an approver (plan, steps, autonomy level, reasons; history), and fix PRs waiting for your merge in GitHub | Approve / Reject with reason (approver). Prod code is approved by merging in GitHub |
| **Requests & ledger** | Every signed ActionRequest: agent, capability, env, autonomy level, approval, result, ledger state (RESERVED / SUCCESS / FAILED / UNKNOWN), idempotency key | – |
| **Known errors** | KEDB entries: approved, proposed by agents, retired; signature, runbook, fix hint, provenance | Approve proposals / retire entries (admin) |
| **Cost & FinOps** | Compute per env per day (billing table or estimate), top pipelines, LLM tokens per day, spend per incident, budget meters; 7/14/30-day filter | – |
| **Agent observability** | LLM calls: model, tokens, latency, redactions, errors; per-model stats | – |
| **Audit log** | Filter by actor, action, target; CSV export | – |
| **Settings & controls** | Kill switch and dry-run switch, **agent registry** (what each agent may request), **autonomy rules** with the bundle hash, platforms (secret refs only), plugins, policies, budgets, LLM routing, fix config, GitHub identity, control history | Kill switch / dry run (admin) |

## Roles and security
| Role | Can |
|---|---|
| viewer | see everything (the API never returns secret values) |
| operator | cancel and rerun, re-run diagnosis, take over incidents |
| approver | start an agent fix |
| admin | kill switch, dry run |

- **Enforced server-side** on every endpoint (403 otherwise). The UI only hides buttons.
- **Every human action needs a reason** and is audited as `user:<name> (<role>)`.
- **PR approval stays in GitHub**, which enforces reviews and records the approver. The console links to it but can't approve.
- **Auth:**
  - `AGENT_CONSOLE_AUTH=demo` (default) signs HMAC session tokens for any name and role. That proves the RBAC model but **is not authentication**.
  - Pilot: `oidc` with Entra ID, mapping roles from Entra groups (see "Next" below).
  - Set `AGENT_CONSOLE_SECRET` so sessions survive restarts.
- **Headers:**
  - strict CSP (`script-src 'self'`; `style-src` needs `'unsafe-inline'` for Fluent's runtime styles)
  - `frame-ancestors 'none'`, `nosniff`, `no-referrer`
  - `no-store` on API responses
- **Error handling:** internal errors return a generic message, and stack traces stay in the server log.
- **Static files:** served with a path-traversal guard.
- **Kill switch:** controls live in the `agent_controls` table. The agent reads them every tick. The console can only turn the agent **off**; `.env AGENT_ENABLED=false` always wins.

## Design system
- **Fluent UI v9** themed with UST brand colours:
  - Dark Teal `#006E74` brand ramp and app bar
  - Soft Black text, Light Gray Wash page background
  - Aptos, falling back to Segoe UI
- **Light and dark themes:** dark is a chosen theme with its own chart steps.
- **Charts (ECharts, tree-shaken, SVG):** follow the dataviz method.
  - The categorical palette was **validated with `validate_palette.js`** in both modes, all pairs (`frontend/src/viz.ts`).
  - Light: `#0097ac #eb6834 #6250d6` on `#ffffff`. Dark: `#0097ac #d95926 #9085e9` on `#292929`.
  - Colour follows the entity: prod teal, qa orange, dev violet.
  - UST Dark Teal fails the chroma floor at chart scale (reads gray), so it stays on chrome only.
  - Status colours (good/warning/serious/critical) only ever mean state, always with an icon and label.
  - Thin bars with 4px rounded ends and 2px gaps between stacked segments, a solid hairline grid, one axis per chart.
  - Every chart has a **Table** tab with the same data, and a tooltip on hover.
- **Accessibility:**
  - status never colour-alone
  - keyboard-focusable lifecycle steps
  - `aria-label`s on charts and tables
  - tabular numbers only in table columns

## Data freshness
- **Polling:**
  - most views every 15 s
  - live runs every 10 s
  - costs and settings every 60 s
- **No flicker:** the previous data stays on screen while refreshing.
- **Platform state** comes from the observer's snapshots, refreshed on every data-plane poll. The platform's `last_poll` shows how fresh they are. Costs are refreshed every 30 min (`system.billing.usage`, or a duration estimate).
- **Server-side cache:** GitHub diffs, 60 s.
- **After an action:** every query is refreshed.

## Hosting
- **Docker:** one image, three commands: `dataplane` and `control` (one active instance each, enforced by leases) and `console` (can scale out).
  - The multi-stage `Dockerfile` builds the UI with Node, then serves it from the Python image.
  - `docker-compose.yml` runs the three services plus Postgres, each with its own env file and key mounts, with the console on localhost only.
- **Pilot:** Azure Container Apps (console behind Front Door + WAF, Entra ID).
- **CI:** the `console-build` job type-checks and builds the UI on every PR.

## Next
- **Entra ID OIDC:** MSAL in the SPA; the API validates tokens (issuer and audience) and maps roles from the `groups` or `roles` claim.
- **Live updates:** Server-Sent Events instead of polling.
- **Typed API client:** generated from OpenAPI, once the views have response models.
- **End-to-end tests:** Playwright E2E and axe accessibility checks in CI.
- **More screens:** an onboarding wizard rendered from each plugin's config schema, and a Teams tab app.
