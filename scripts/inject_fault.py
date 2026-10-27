"""Inject one fault scenario into the pipelines repo / workspace (docs/10-demo-runbook.md).

    python scripts/inject_fault.py list
    python scripts/inject_fault.py bad_cast                                   # data fault in prod + run orders_pipeline
    python scripts/inject_fault.py slow_report                                # runtime fault
    python scripts/inject_fault.py typo_column --repo-path ../orders-pipelines --push   # release fault on main
    python scripts/inject_fault.py reset-git --repo-path ../orders-pipelines  # dev/qa/main back to demo-baseline

Release faults commit straight to the pipelines repo's `main`. That bypasses the
dev -> qa -> main flow on purpose (it simulates a bad hotfix) and needs a repo admin.
Data faults run the repo's setup/10_seed_scenario notebook in Databricks. Demo only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from _common import GIT_FOLDER_ROOT, client, confirm, git_folder, run_job, run_notebook

BASELINE_TAG = "demo-baseline"
BRANCHES = ("dev", "qa", "main")


@dataclass(frozen=True)
class Fault:
    number: int
    kind: str  # data | runtime | release | env | guardrail
    description: str


FAULTS: dict[str, Fault] = {
    "slow_report": Fault(1, "runtime", "sales_report with sleep_seconds=900 (threshold 300s)"),
    "flaky_partner": Fault(2, "runtime", "partner_feed_ingest with flaky_rate=0.7"),
    "bad_cast": Fault(3, "data", "qty='abc' -> CAST_INVALID_INPUT"),
    "zero_units": Fault(4, "data", "store with qty 0 -> DIVIDE_BY_ZERO in build_gold"),
    "duplicate_keys": Fault(5, "data", "duplicate order_id -> MERGE fails"),
    "schema_drift": Fault(6, "data", "qty renamed to quantity -> UNRESOLVED_COLUMN"),
    "null_keys": Fault(7, "data", "null store_id -> DQAssertionError"),
    "typo_column": Fault(8, "release", "column typo committed to main"),
    "hardcoded_catalog": Fault(9, "release", "hard-coded catalog committed to main"),
    "syntax_error": Fault(10, "release", "syntax error in the silver DQ cell committed to main"),
    "missing_secret": Fault(11, "env", "delete secret partner-prod/api_key -> 'Secret does not exist'"),
    "prompt_injection": Fault(12, "data", "bad value carrying an injected instruction"),
    "secret_in_logs": Fault(13, "runtime", "partner_feed prints a FAKE token, then fails"),
    "budget_exhausted": Fault(14, "guardrail", "very low LLM budget"),
}


def apply_edits(repo: Path, edits: list[dict]) -> list[str]:
    """Replace the first occurrence of each `find`; all targets are checked before anything is written."""
    texts = {}
    for edit in edits:
        text = texts.get(edit["file"]) or (repo / edit["file"]).read_text(encoding="utf-8")
        if edit["find"] not in text:
            raise ValueError(f"{edit['file']}: text to replace not found (fault already applied?)")
        texts[edit["file"]] = text.replace(edit["find"], edit["replace"], 1)
    for name, text in texts.items():
        (repo / name).write_text(text, encoding="utf-8")
    return list(texts)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def inject_release(name: str, repo: Path, push: bool, assume_yes: bool) -> None:
    spec = yaml.safe_load((repo / "faults" / f"{name}.yaml").read_text(encoding="utf-8"))
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        sys.exit(f"Check out main in {repo} first (current branch: {branch})")
    try:
        files = apply_edits(repo, spec["edits"])
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"  edited {', '.join(files)}")
    git(repo, "add", *files)
    git(repo, "commit", "-m", spec["commit_message"])
    if push:
        confirm("Push this faulty commit to origin/main (needs admin bypass of branch protection)?", assume_yes)
        git(repo, "push", "origin", "main")
        print("  pushed; pull the prod Git folder (or let the agent sync it) to roll it out")
    else:
        print("  committed locally; re-run with --push to publish it")


def reset_git(repo: Path, assume_yes: bool) -> None:
    git(repo, "fetch", "origin")
    sha = git(repo, "rev-list", "-n", "1", BASELINE_TAG)
    for branch in BRANCHES:
        ahead = git(repo, "rev-list", "--count", f"{BASELINE_TAG}..origin/{branch}")
        print(f"  origin/{branch}: {ahead} commit(s) after {BASELINE_TAG} will be discarded")
    confirm(f"Force-push dev, qa and main to {BASELINE_TAG} ({sha[:8]})?", assume_yes)
    for branch in BRANCHES:
        git(repo, "push", "--force", "origin", f"{sha}:refs/heads/{branch}")
        print(f"  origin/{branch} -> {BASELINE_TAG}")
    print("  close leftover demo PRs in GitHub and pull the three Git folders in Databricks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=["list", "reset-git", *FAULTS])
    parser.add_argument("--env", default="prod", choices=["dev", "qa", "prod"])
    parser.add_argument("--layout", default="catalog", choices=["catalog", "schema"])
    parser.add_argument("--repo-path", type=Path, help="local clone of the pipelines repo (release faults, reset-git)")
    parser.add_argument("--git-folder-root", default=GIT_FOLDER_ROOT, help="Databricks Git folders are <root>-<env>")
    parser.add_argument("--push", action="store_true", help="release faults: push to origin/main")
    parser.add_argument("--no-run", action="store_true", help="data faults: seed only, don't start the job")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    if args.scenario == "list":
        for name, f in FAULTS.items():
            print(f"  {f.number:>2}  {name:<18} {f.kind:<9} {f.description}")
        return
    if args.scenario == "reset-git" or FAULTS[args.scenario].kind == "release":
        if not args.repo_path:
            sys.exit("--repo-path (local clone of the pipelines repo) is required")
        repo = args.repo_path.resolve()
        if args.scenario == "reset-git":
            reset_git(repo, args.yes)
        else:
            inject_release(args.scenario, repo, args.push, args.yes)
        return

    fault = FAULTS[args.scenario]
    print(f"Scenario {fault.number}: {args.scenario} ({fault.kind}) -> {fault.description}")
    if fault.kind == "guardrail":
        sys.exit("Lower budgets.per_incident_usd in config/budgets.yaml, then run bad_cast.")

    w = client()
    if fault.kind == "data":
        seed = f"{git_folder(args.env, args.git_folder_root)}/setup/10_seed_scenario"
        run_notebook(w, seed, {"env": args.env, "layout": args.layout, "scenario": args.scenario})
        if not args.no_run:
            run_job(w, "orders_pipeline", args.env)
    elif args.scenario == "slow_report":
        run_job(w, "sales_report", args.env, {"sleep_seconds": "900"})
    elif args.scenario == "flaky_partner":
        run_job(w, "partner_feed_ingest", args.env, {"flaky_rate": "0.7"})
    elif args.scenario == "secret_in_logs":
        run_job(w, "partner_feed_ingest", args.env, {"leak_fake_token": "true"})
    elif args.scenario == "missing_secret":
        w.secrets.delete_secret(scope=f"partner-{args.env}", key="api_key")
        print(f"  deleted secret partner-{args.env}/api_key (setup/99_reset_demo restores it)")
        run_job(w, "partner_feed_ingest", args.env)


if __name__ == "__main__":
    main()
