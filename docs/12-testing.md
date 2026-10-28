# 12 · Testing

| Suite | Command | Needs | What it covers |
|---|---|---|---|
| Agent + plugins | `pytest` | venv only, no network | Everything below |
| Console UI | `cd frontend; npm run build` | Node | Strict TypeScript + production bundle |
| Pipeline notebooks | runs in Databricks | the pipelines repo | `tests/test_*` notebooks (the Testing agent adds more per fix); CI there runs `notebook-lint` |
| CI | `.github/workflows/ci.yml` | GitHub | `agent-tests`, `console-build` |

Most end-to-end tests run **both planes in-process over the simulator**:
- the data plane's observer and executor;
- the control plane's LangGraph runner with an in-memory checkpointer.

Only signed requests and events pass between the two planes, exactly as in production. Every `AGENT_*` variable is removed for tests (an autouse fixture), so a developer's `.env` can't change the results.

## Suites

| File | Covers |
|---|---|
| [test_boundary.py](../tests/test_boundary.py) | Ed25519 sign/verify; tampering per field; unknown key; expiry; idempotent submit; single claim; ledger replay and UNKNOWN sweep; leases |
| [test_foundations.py](../tests/test_foundations.py) | Autonomy matrix cases, caps and circuit breaker, kill switch and dry run, registry denial, bundle hash; agent registry; KEDB seeding, curation and per-platform names; evidence redaction and integrity; model aliases |
| [test_data_plane.py](../tests/test_data_plane.py) | Observer events (once per run, redacted evidence, slow runs, watched runs); executor happy paths (rerun, tests, sync, prod repair, profile); refusals (replay, tampered, untrusted, policy drift, policy re-check, live preconditions); dry run; platform error → FAILED; bad sync → UNKNOWN; one active data plane |
| [test_control_plane.py](../tests/test_control_plane.py) | The legacy monitor scenarios on the graph: transient → resolved; rerun fails → escalated without a second incident; code → KEDB → parked; permission → escalated; dev → recommend; slow → one approval → cancel+rerun; reject; approval timeout; kill switch; dry run; reconciliation finds duplicates; restart resumes without resubmitting; secrets redacted; LLM only without a KEDB answer; budget stop; one active control plane |
| [test_code_fix.py](../tests/test_code_fix.py) | Full code-fix path (Databricks tests → dev/qa merge, sync, smoke → human prod merge → prod sync + repair → resolved + KEDB proposal); failing tests → repair; failing CI → repair on the same PR; repairs exhausted; prod PR closed; no `auto_fix`; Testing-agent guards |
| [test_console_api.py](../tests/test_console_api.py) | Auth, tampered token, security headers; overview over both planes; incident detail with evidence, requests, graph state and approvals; redacted evidence; console cancel/rerun → L3 signed request executed by the data plane; approver approval resumes a paused graph (403 for operators, 409 twice); kill switch; takeover stops the graph; KEDB curation is admin-only; agents and policy views; snapshot-only platform state (no platform access); no secret values in settings |
| [test_llm.py](../tests/test_llm.py) | Gateway ledger, cost, redaction, fallback, budgets; triage parsing, fallbacks, injection heuristic; graph integration: diagnosis stored, budget stop, LLM outage, prompt-injection flag |
| [test_fix.py](../tests/test_fix.py) | Every patch guard of the Recovery fix writer (paths, compile, pyflakes, protected and forbidden patterns, tests, secrets) |
| [test_github_client.py](../tests/test_github_client.py) | GitHub request shapes through `httpx.MockTransport` |
| [test_core_units.py](../tests/test_core_units.py), [test_demo_kit.py](../tests/test_demo_kit.py) | Redaction, classification, config merge, secrets, p95; fault-kit mechanics |
| plugin `tests/` | Contract suites for both plugins; Databricks mapping and actions (`repair_run`, Git-folder sync, `git_source` test submit, SQL profile, definition hash) against a fake client |

## Live checks
- `python scripts/phase0_checks.py --redesign`: Git folders, a test notebook from a GitHub branch, `dbutils.notebook.run` on serverless, `repair_run`, SQL warehouse and `system.lakeflow` (all passed 2026-10-27).
- Two real processes over the simulator (`dataplane --once` / `control --once`, 8 separate processes): a transient failure resolved across checkpoint restarts (2026-10-27).
- A live signed `tests.run` executed in the Databricks workspace from the `dev` branch: `run_finished: succeeded` (2026-10-27).
- Console smoke test over both planes: every new endpoint 200; a console rerun executed as an L3 signed request, ledger `SUCCESS` (2026-10-28).

## Latest local result (2026-10-28)
- `pytest`: **187 passed**.
- `npm run build`: clean.
