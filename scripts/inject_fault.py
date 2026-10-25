"""Inject one fault scenario (docs/10-demo-runbook.md has the full catalog).

    python scripts/inject_fault.py list
    python scripts/inject_fault.py bad_cast            # data fault in prod + run orders_pipeline
    python scripts/inject_fault.py slow_report         # runtime fault
    python scripts/inject_fault.py typo_column --push  # release fault: commit on main and push

Release faults commit straight to `main`. That bypasses the dev -> qa -> main
flow on purpose (it simulates a bad hotfix) and needs a repo admin. Demo only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass

import yaml

from _common import REPO_ROOT, client, confirm, replace_landing, run_job
from generate_data import scenario_rows, to_csv


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
    "syntax_error": Fault(10, "release", "syntax error in common/dq.py committed to main"),
    "missing_secret": Fault(11, "env", "delete secret partner-prod/api_key -> 'Secret does not exist'"),
    "prompt_injection": Fault(12, "data", "bad value carrying an injected instruction"),
    "secret_in_logs": Fault(13, "runtime", "partner_feed prints a FAKE token, then fails"),
    "budget_exhausted": Fault(14, "guardrail", "very low LLM budget (needs Phase 2 LLM gateway)"),
}


def inject_release(name: str, push: bool, assume_yes: bool) -> None:
    spec = yaml.safe_load((REPO_ROOT / "sample_pipelines" / "faults" / f"{name}.yaml").read_text(encoding="utf-8"))
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if branch != "main":
        sys.exit(f"Check out main first (current branch: {branch})")
    for edit in spec["edits"]:
        path = REPO_ROOT / edit["file"]
        text = path.read_text(encoding="utf-8")
        if edit["find"] not in text:
            sys.exit(f"{edit['file']}: text to replace not found (fault already applied?)")
        path.write_text(text.replace(edit["find"], edit["replace"], 1), encoding="utf-8")
        print(f"  edited {edit['file']}")
    files = [e["file"] for e in spec["edits"]]
    subprocess.run(["git", "add", *files], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "commit", "-m", spec["commit_message"]], cwd=REPO_ROOT, check=True)
    if push:
        confirm("Push this faulty commit to origin/main (needs admin bypass of branch protection)?", assume_yes)
        subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
        print("  pushed; the deploy workflow will roll it out to prod")
    else:
        print("  committed locally; re-run with --push to deploy it")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=["list", *FAULTS])
    parser.add_argument("--env", default="prod", choices=["dev", "qa", "prod"])
    parser.add_argument("--layout", default="catalog", choices=["catalog", "schema"])
    parser.add_argument("--push", action="store_true", help="release faults: push to origin/main")
    parser.add_argument("--no-run", action="store_true", help="data faults: seed only, don't start the job")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    if args.scenario == "list":
        for name, f in FAULTS.items():
            print(f"  {f.number:>2}  {name:<18} {f.kind:<9} {f.description}")
        return

    fault = FAULTS[args.scenario]
    print(f"Scenario {fault.number}: {args.scenario} ({fault.kind}) -> {fault.description}")
    if fault.kind == "release":
        inject_release(args.scenario, args.push, args.yes)
        return
    if fault.kind == "guardrail":
        sys.exit("Needs the Phase 2 LLM gateway. Lower budgets.per_incident_usd in config/budgets.yaml, then run bad_cast.")

    w = client()
    if fault.kind == "data":
        replace_landing(w, args.env, to_csv(*scenario_rows(args.scenario)), args.layout)
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
        print(f"  deleted secret partner-{args.env}/api_key (reset_demo.py restores it)")
        run_job(w, "partner_feed_ingest", args.env)


if __name__ == "__main__":
    main()
