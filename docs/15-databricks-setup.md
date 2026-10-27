# 15 · Databricks setup (pipelines repo, Git folders, jobs)

The agent monitors pipelines that live in their **own GitHub repo** (`karthik-np-ust/orders-pipelines`),
like a customer's. The agent never deploys pipelines: you set them up once, and code reaches
Databricks only through PRs and Git-folder pulls.

```
GitHub: orders-pipelines            Databricks workspace
  dev  ───────────────────────────▶  /Workspace/Shared/orders-pipelines-dev   ◀── [dev] jobs
  qa   ───────────────────────────▶  /Workspace/Shared/orders-pipelines_qa    ◀── [qa] jobs
  main ───────────────────────────▶  /Workspace/Shared/orders-pipelines_prod  ◀── prod jobs
```

## Steps

1. **Create the GitHub repo** and push the local scaffold (`C:\Users\285509\orders-pipelines`):
   ```bash
   cd C:\Users\285509\orders-pipelines
   git remote add origin https://github.com/karthik-np-ust/orders-pipelines.git
   git push -u origin main
   git push origin main:dev main:qa
   git tag demo-baseline && git push origin demo-baseline   # used by inject_fault.py reset-git
   ```
   Make it public (free branch protection). Protect `dev`, `qa` and `main`:
   - require a PR and the `notebook-lint` check;
   - on `qa` and `main`, also require `promotion-path` and Code Owner review.
2. **Agent access.** Install the agent's GitHub App (`pipeline-agent-karthik`) on the new repo. Set the repo variable `AGENT_BOT_LOGIN` to its bot login so `path-guard` applies to its PRs.
3. **Link GitHub in Databricks:** User settings → Linked accounts → GitHub.
4. **Git folders:** Workspace → Shared → Create → **Git folder** → repo URL.
   - Name it `orders-pipelines-dev` and switch it to branch `dev`.
   - Repeat for `orders-pipelines_qa` (branch `qa`) and `orders-pipelines_prod` (branch `main`).
5. **Demo data:** open `orders-pipelines-dev/setup/00_setup_demo` and **Run all**.
   - It creates the catalogs `dev`, `qa` and `prod` with schemas `bronze`, `silver` and `gold`, the `bronze.landing` volume, the secret scopes `partner-<env>` and clean data.
   - If catalogs can't be created, use `layout = schema`. Then set `catalog = workspace` and `layout = schema` on the jobs.
6. **Jobs:** create the 9 jobs from `jobs/<env>/*.json` in the pipelines repo, using either method:
   - **UI:** Jobs & Pipelines → Create job. Add notebook tasks with the paths, dependencies, parameters and schedule from the JSON. Leave compute as serverless.
   - **API:**
     ```bash
     for f in jobs/*/*.json; do
       curl -sS -X POST "$DATABRICKS_HOST/api/2.2/jobs/create" -H "Authorization: Bearer $DATABRICKS_TOKEN" -d @"$f"; echo
     done
     ```
   Job parameters (`catalog`, `env`, `layout`, …) reach the notebooks as widgets. The dev and qa schedules are paused; prod runs daily.
7. **Smoke test:**
   - Run `[dev] orders_pipeline`.
   - Run each `orders-pipelines-dev/tests/test_*` notebook. Every one should print `PASSED` and leave no `test_*` schemas behind.

## How the agent uses this (target design)

| Step | Who | How |
|---|---|---|
| Detect a failed run | data-plane observer | Jobs API (`jobs.list_runs`, `get_run_output`) |
| Test a fix | Testing agent → data plane `tests.run` | one-time serverless run of the fix branch's test notebooks |
| Ship a fix to an environment | PR merge, then data plane `code.sync` | Repos API: update the env's Git folder to the latest commit |
| Re-run only what failed | data plane `pipeline.repair` | Jobs `repair_run` |

## Fault scenarios

See the pipelines repo README ("Fault scenarios"). Release faults are applied from this repo:
```bash
python scripts/inject_fault.py typo_column --repo-path ../orders-pipelines --push
python scripts/inject_fault.py bad_cast                     # seeds data via setup/10_seed_scenario, runs the prod job
python scripts/inject_fault.py reset-git --repo-path ../orders-pipelines
```
