# 10 · Demo runbook

## A. Credential-free demo (simulator)

```powershell
.\.venv\Scripts\pipeline-agent keys generate --out var/keys   # once
.\.venv\Scripts\pipeline-agent kedb seed                        # once (idempotent)
# In config/platforms.yaml set dbx-free.enabled: false for a pure simulator demo.

# terminal 1                                     # terminal 2
.\.venv\Scripts\pipeline-agent dataplane        .\.venv\Scripts\pipeline-agent control
# terminal 3 (optional)
.\.venv\Scripts\pipeline-agent console          # http://127.0.0.1:8080, demo login as approver
```

What you'll see:
- **About 1 minute in:**
  - `partner_feed_ingest` (transient): rerun at L4 and resolved;
  - `customer_export` (permission): escalated, no action;
  - `orders_pipeline` (code): diagnosed and parked "needs a code fix", unless the fix path is on.
- **About 5 minutes in:** `sales_report` passes its threshold, and an approval appears.
  - `pipeline-agent approvals approve --id N`, or **Approvals** in the console.
  - The cancel and rerun are then executed, watched and reconciled.

Use `--once` on either process to step through the demo tick by tick. To start again, delete `agent_state.db`, `.sim_state.json` and `var/checkpoints.sqlite`.

## B. Databricks Free Edition demo

### Prerequisites
- The pipelines repo, Git folders and jobs set up per [15 · Databricks setup](15-databricks-setup.md).
- `.env` from `.env.example` with `DATABRICKS_HOST`, `DATABRICKS_TOKEN` and `DATABRICKS_WAREHOUSE_ID` (data plane only).
- Environment checks: `python scripts/phase0_checks.py --redesign`. Results from 2026-10-27 are in [04](04-scope-and-status.md).
- Start in **dry run** (`AGENT_DRY_RUN=true`): every decision is made, signed and checked, but nothing executes.

```powershell
.\.venv\Scripts\pipeline-agent check        # dbx-free: health=ok (authenticated; N jobs visible)
.\.venv\Scripts\pipeline-agent dataplane    # terminal 1
.\.venv\Scripts\pipeline-agent control      # terminal 2
```

### LLM triage and code fixes
In `.env`:
- `OPENROUTER_API_KEY`;
- `AGENT_LLM_ENABLED=true`, and for fixes `AGENT_FIX_ENABLED=true`;
- the GitHub App (`GITHUB_APP_*`) installed on the pipelines repo.

```powershell
.\.venv\Scripts\pipeline-agent llm          # budgets, today's usage, provider key status
.\.venv\Scripts\pipeline-agent github       # the agent's identity and the dev/qa/main heads of the pipelines repo
.\.venv\Scripts\pipeline-agent fix 3        # ask for a fix of incident 3 (same as "Start fix" in the console)
```

**Free-tier LLM budget:** 45 requests a day. A code fix uses about 2–5 of them (diagnosis, fix, tests, plus one per repair). Transient and infra failures that match the KEDB use none.

## C. Fault scenario catalog

`python scripts/inject_fault.py list` shows them all. Data faults run the pipelines repo's `setup/10_seed_scenario` notebook in Databricks; release faults commit to a local clone (`--repo-path`). Reset with `setup/99_reset_demo` (data) and `inject_fault.py reset-git --repo-path ../orders-pipelines` (code).

| # | Command | Expected behaviour |
|---|---|---|
| 1 | `inject_fault.py slow_report` | `run_slow` → approval (L3) → cancel + rerun → watched → resolved; or rejected → escalated |
| 2 | `inject_fault.py flaky_partner` | KEDB `network_transient` → rerun (L4) → resolved; a second failure → escalated, no second incident |
| 3 | `inject_fault.py bad_cast` | KEDB `cast_invalid_input` → diagnosis → fix → Databricks tests → dev, qa → prod PR waits for your merge → prod repair → resolved, KEDB proposal |
| 4–7 | `zero_units`, `duplicate_keys`, `schema_drift`, `null_keys` | As 3. `null_keys`: the fix must handle the rows, never remove `DQAssertionError` (the guard rejects that) |
| 8–10 | `typo_column`, `hardcoded_catalog`, `syntax_error` with `--repo-path ../orders-pipelines --push` | As 3 (release faults on `main`, via admin bypass, deliberately) |
| 11 | `inject_fault.py missing_secret` | KEDB `secret_missing` → **escalate** (L1), no rerun, no PR |
| 12 | `inject_fault.py prompt_injection` | Diagnosis flagged **suspicious** (audit `suspicious_content`, critical notification); any fix still goes through the guards and the PR path guard |
| 13 | `inject_fault.py secret_in_logs` | The fake `dapiFAKE…` token is `[REDACTED:DATABRICKS_TOKEN]` in evidence, incident, notification, audit and prompts |
| 14 | `per_day_requests: 0` in `budgets.yaml`, then any code fault | `llm_budget_stop` → escalated |

### 20-minute live demo order
1. **Scenario 2 (transient):** show **Requests & ledger**, with an L4 signed request at `SUCCESS`.
2. **Scenario 1 (slow):** approve it in **Approvals** and watch the cancel and rerun.
3. **Scenario 11 (secret missing):** escalated with evidence, nothing executed.
4. **Scenario 13 (secret in logs):** open the evidence viewer and show the redaction.
5. **Scenario 3 (bad cast):** open the incident's **Agents on this incident** panel, then:
   - the Databricks test run;
   - the dev/qa PRs merged by the agent;
   - merge the prod PR yourself;
   - the prod repair, then resolved;
   - the proposed KEDB entry under **Known errors**.
