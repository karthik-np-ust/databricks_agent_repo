# 12 · Testing

| Suite | Command | Needs | What it covers |
|---|---|---|---|
| Core + plugins | `pytest` | venv only | Monitor/policy scenarios on the simulator, redaction, classification, config merge, secrets, registry and API versioning, notifications, demo-kit consistency, both plugin contract suites, Databricks plugin mapping against a fake client |
| Sample pipelines | `pytest sample_pipelines/tests -rs` | `pip install -r sample_pipelines/requirements-dev.txt` + Java 17 for the Spark tests | Table naming (pure Python) and Spark transforms with ANSI mode on, like serverless |
| CI | `.github/workflows/ci.yml` | GitHub | Both suites, agent path guard, `bundle validate` |

## Scenario tests ([tests/test_monitor.py](../tests/test_monitor.py))
Each one drives the real Monitor + PolicyEngine + StateStore against the simulator with a fake clock:
- healthy run → no action
- scenario 1: slow run → cancel + rerun twice → escalation on the 3rd breach, once
- scenario 2: transient → one rerun → recovers, no incident. Repeated failures → escalation.
- scenario 3: code failure → incident `cast_invalid_input`, fix-eligible only with `auto_fix`, never rerun
- scenario 11: permission failure → escalation, no rerun
- scenario 13: fake token redacted in incident, notifications and audit
- dev env → notify only
- kill switch, dry run, circuit breaker, p95 baseline threshold
- platform without a controller → escalation
- one broken platform doesn't stop the others

## Demo kit consistency ([tests/test_demo_kit.py](../tests/test_demo_kit.py))
- Every data scenario produces the intended bad data.
- Every release-fault edit still matches exactly one place in the current code, so the fault kit can't silently rot when the pipelines change.
- The syntax-error fault really is a syntax error.
- The fault catalog numbers cover 1–14.

## LLM tests ([tests/test_llm.py](../tests/test_llm.py))
Run with a fake OpenAI client (no network). They cover:
- ledger and cost
- redaction before sending
- model fallback and all-models-fail
- daily request budget and per-incident token budget
- tool-call parsing and JSON-in-content fallback
- unusable answers
- injection heuristic
- monitor integration: diagnosis stored, scenario 14 budget stop, LLM outage, scenario 12 flag

Tests run with every `AGENT_*` variable removed (autouse fixture), so a developer's `.env` can't change results.

## Live checks
- `python scripts/phase0_checks.py`: real Databricks and OpenRouter.
- `AGENT_LLM_ENABLED=true pipeline-agent once` (twice, about 50 s apart) against the simulator: real diagnoses, visible in `pipeline-agent incidents --show 1` and `pipeline-agent llm`.

## Fix flow tests ([tests/test_fix.py](../tests/test_fix.py), [tests/test_github_client.py](../tests/test_github_client.py))
These use an in-memory fake GitHub (branches, commits, PRs, checks, deploy runs, human merge/close) and a fake LLM. They cover:
- every patch guard
- the full flow dev → qa → human gate → prod deploy → prod rerun → resolved, including that the agent never merges `main` and asks for approval only once
- validation retry and no-valid-patch escalation
- CI failure (PR comment, escalation), reviewer rejection, protected `qa` waiting for a human
- deploy failure, timeout, dry run, the one-fix-at-a-time limit
- GitHub client request shapes through `httpx.MockTransport`

## Console tests ([tests/test_console_api.py](../tests/test_console_api.py))
The API runs over a store that the real Monitor wrote, through the simulator. The tests cover:
- login required, tampered token rejected, security headers
- overview KPIs, pipelines, incident detail and audit, active runs with threshold progress
- RBAC: a viewer can't act, an operator can, and the action is audited with name and reason; a reason is mandatory
- the admin kill switch reaches the agent's next tick; take-over; settings never expose secret values

UI: `npm run build` (strict TypeScript + production bundle), also in CI (`console-build`).
Live smoke test: all 15 read endpoints return 200 on real data (Databricks billing, GitHub diff), and a viewer action returns 403.

## Latest local result (2026-10-26)
- `pytest`: **148 passed**.
- `pytest sample_pipelines/tests`: **3 passed, 1 skipped** (Spark module skipped: no PySpark/Java on this machine).
