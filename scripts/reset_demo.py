"""Reset the demo between fault scenarios.

Default (safe, workspace only):
  - clean synthetic data back in every env's landing volume
  - partner-<env>/api_key secrets restored
  - silver/gold tables dropped so the next run starts fresh

Optional:
  --agent-state   delete the local agent state DB and simulator state
  --git           reset dev, qa and main to the `demo-baseline` tag and FORCE-PUSH them.
                  This discards every commit after the tag on those branches. Asks first.

    python scripts/reset_demo.py
    python scripts/reset_demo.py --git --agent-state
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from _common import ENVS, REPO_ROOT, client, confirm, replace_landing, sql, tables
from generate_data import scenario_rows, to_csv
from setup_workspace import DUMMY_KEY

BASELINE_TAG = "demo-baseline"
BRANCHES = ("dev", "qa", "main")


def reset_workspace(layout: str) -> None:
    w = client()
    for env in ENVS:
        print(f"[{env}]")
        t = tables(env, layout)
        replace_landing(w, env, to_csv(*scenario_rows("clean")), layout)
        w.secrets.put_secret(scope=f"partner-{env}", key="api_key", string_value=DUMMY_KEY)
        for table in (t.silver_orders, t.gold_daily_sales, t.gold_sales_report):
            sql(w, f"DROP TABLE IF EXISTS {table}", check=False)
        print("  data, secret and tables reset")


def reset_git(assume_yes: bool) -> None:
    git = lambda *a: subprocess.run(["git", *a], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout.strip()
    sha = git("rev-list", "-n", "1", BASELINE_TAG)
    for branch in BRANCHES:
        ahead = git("rev-list", "--count", f"{BASELINE_TAG}..origin/{branch}")
        print(f"  origin/{branch}: {ahead} commit(s) after {BASELINE_TAG} will be discarded")
    confirm(f"Force-push dev, qa and main to {BASELINE_TAG} ({sha[:8]})?", assume_yes)
    for branch in BRANCHES:
        git("push", "--force", "origin", f"{sha}:refs/heads/{branch}")
        print(f"  origin/{branch} -> {BASELINE_TAG}")
    print("  close leftover demo PRs in GitHub (they now point at discarded commits)")


def reset_agent_state() -> None:
    for name in ("agent_state.db", ".sim_state.json"):
        path = Path(REPO_ROOT / name)
        if path.exists():
            path.unlink()
            print(f"  removed {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=["catalog", "schema"], default="catalog")
    parser.add_argument("--git", action="store_true")
    parser.add_argument("--agent-state", action="store_true")
    parser.add_argument("--skip-workspace", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.skip_workspace:
        reset_workspace(args.layout)
    if args.git:
        reset_git(args.yes)
    if args.agent_state:
        reset_agent_state()
    print("done")


if __name__ == "__main__":
    main()
