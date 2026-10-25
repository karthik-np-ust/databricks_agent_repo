# 13 · GitHub setup (deploy path for the demo)

Uploads from the UST laptop to Databricks are blocked by Zscaler, so everything that uploads runs on GitHub-hosted runners:
- bundle deploys
- data seeding
- data fault injection

The agent itself keeps running on the laptop, because monitoring only uses JSON API calls.

```
laptop ──git push──► GitHub ──Actions──► Databricks (deploy, seed data, inject faults)
laptop ──pipeline-agent (JSON API: poll, cancel, rerun, logs, billing)──► Databricks
```

## 0. Before the first push (done 2026-10-25, re-check anyway)
- `.env.example` contains placeholders only. Real values live in `.env`, which is git-ignored.
- `git status --ignored` lists `.env`, `.venv/` and `*.db` as ignored.
- Scan for secret-looking strings: `git ls-files --cached --others --exclude-standard | xargs grep -lE "dapi[0-9a-f]{32}|sk-or-v1-[0-9a-f]{20,}"` should print nothing.

## 1. Choose where the repo lives
| Option | Enforced approvals (branch protection, Environment reviewers) | Note |
|---|---|---|
| **UST GitHub Enterprise** (preferred) | ✅ | Check that GitHub-hosted runners are allowed in your org |
| Personal account, **public** repo | ✅ free | Only if UST policy allows publishing this code. The repo holds no secrets or workspace URL. |
| Personal account, private repo, free plan | ❌ (rules shown, not enforced) | Fine for deploys, not for a real approval gate |

## 2. Create the repo
GitHub → **New repository** → name e.g. `databricks-agent-demo` → **no** README, .gitignore or license (the repo must be empty).

## 3. Add secrets *before* pushing
The first push triggers a deploy, so add these first under Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | workspace URL from your `.env` |
| `DATABRICKS_TOKEN` | Databricks PAT. Better: create a **new** PAT for CI and rotate the one shared in chat. |
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse ID from your `.env` |

Repo **variable** (same page, Variables tab): `AGENT_BOT_LOGIN` = the GitHub login the agent will use for PRs in Phase 3. If you don't have one yet, use `not-set-yet`.

## 4. Environments
**Settings → Environments**: create `dev`, `qa` and `prod`. On `prod`, tick **Required reviewers** and add yourself, so every prod deploy waits for your approval.

## 5. Personalise ownership files
- `.github/CODEOWNERS`: replace `@your-org/data-platform-approvers` and `@your-org/platform-admins` with your GitHub username (e.g. `@karthik-np`).
- `config/platforms.yaml`: set `repo:` (both instances) to `<owner>/<repo>`.

## 6. First push (main only)
```powershell
cd C:\Users\285509\databricks_agent
git config user.name  "<your name>"
git config user.email "<your GitHub email>"
git add -A
git status            # check: no .env, no .venv, no *.db
git commit -m "Pipeline production support agent: Phases 0-2"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```
The push starts **deploy → prod** (Actions tab). Approve it when asked.

## 7. Seed the workspace (from GitHub)
Actions → **demo-ops** → Run workflow:
- branch `main`
- action `setup`
- layout `catalog`

This creates the `qa` and `prod` catalogs (dev already exists), plus schemas, landing volumes, secret scopes `partner-<env>` and clean synthetic data.

## 8. Create the dev and qa branches
```powershell
git push origin main:dev
git push origin main:qa
git tag demo-baseline
git push origin demo-baseline
```
If the deploy workflow didn't start for the new branches: Actions → **deploy** → Run workflow → pick `dev`, then `qa`. Each runs `bundle deploy` plus a smoke run of `[dev] orders_pipeline` / `[qa] orders_pipeline`.

## 9. Protect qa and main
**Settings → Branches → Add rule** (or Rulesets), for `main` and for `qa`:
- Require a pull request before merging, with 1 approval and **Require review from Code Owners**.
- Require status checks: `agent-tests`, `pipeline-tests`, `bundle-validate`.
- Block force pushes.
- Leave "allow administrators to bypass" **on** for the demo. `reset_demo.py --git` and release-fault injection need it.

> GitHub doesn't let you approve your own PR. In Phase 3 the agent opens PRs as its own bot account (`AGENT_BOT_LOGIN`) and you approve them. Until then, merge your own PRs with admin bypass.

## 10. Point the agent at the deployed jobs
```powershell
.\.venv\Scripts\pipeline-agent check     # dbx-free: "N jobs visible" (9 = 3 jobs x 3 envs)
.\.venv\Scripts\pipeline-agent run       # AGENT_DRY_RUN=true first; watch the decisions
```

## 11. First fault
Actions → **demo-ops** → action `inject`, scenario `bad_cast`, env `prod`. This uploads bad data and starts prod `orders_pipeline`. Then on the laptop:
```powershell
.\.venv\Scripts\pipeline-agent once
.\.venv\Scripts\pipeline-agent incidents --show 1
```
Reset afterwards with demo-ops action `reset`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `git push` blocked by the proxy | Try GitHub Desktop or the `gh` CLI. If git to GitHub is blocked entirely, ask IT. |
| Push rejected by **push protection** (secret detected) | A real token is in a committed file. Remove it, amend the commit, and rotate the token. |
| `bundle-validate` / deploy: `environment_version` unknown | Older CLI. In `resources/jobs.yml`, replace `environment_version: "2"` with `client: "2"`. |
| Deploy waits forever | The `prod` Environment needs your approval (Actions run page → Review deployments). |
| Smoke run fails with `TABLE_OR_VIEW_NOT_FOUND` / missing landing files | Run demo-ops `setup` first. |
