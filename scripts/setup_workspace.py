"""One-time workspace setup for the demo (Phase 0).

Creates, per environment (dev, qa, prod):
  - catalog <env> (or schemas workspace.<env>_* with --layout schema)
  - schemas bronze, silver, gold and the volume bronze.landing
  - secret scope partner-<env> with a dummy api_key (used by partner_feed_ingest)
  - clean synthetic data in the landing volume

    python scripts/setup_workspace.py               # catalog layout
    python scripts/setup_workspace.py --layout schema
"""

from __future__ import annotations

import argparse

from _common import ENVS, client, replace_landing, sql
from generate_data import scenario_rows, to_csv

DUMMY_KEY = "demo-not-a-real-key"


def setup_env(w, env: str, layout: str) -> None:
    print(f"[{env}]")
    if layout == "catalog":
        sql(w, f"CREATE CATALOG IF NOT EXISTS {env}")
        schemas = [f"{env}.{layer}" for layer in ("bronze", "silver", "gold")]
    else:
        schemas = [f"workspace.{env}_{layer}" for layer in ("bronze", "silver", "gold")]
    for schema in schemas:
        sql(w, f"CREATE SCHEMA IF NOT EXISTS {schema}")
    sql(w, f"CREATE VOLUME IF NOT EXISTS {schemas[0]}.landing")

    scope = f"partner-{env}"
    if scope not in {s.name for s in w.secrets.list_scopes()}:
        w.secrets.create_scope(scope=scope)
    w.secrets.put_secret(scope=scope, key="api_key", string_value=DUMMY_KEY)
    print(f"  secret scope {scope} ready")

    replace_landing(w, env, to_csv(*scenario_rows("clean")), layout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=["catalog", "schema"], default="catalog")
    parser.add_argument("--env", choices=ENVS, action="append", help="limit to these envs (default: all)")
    args = parser.parse_args()
    w = client()
    for env in args.env or ENVS:
        try:
            setup_env(w, env, args.layout)
        except RuntimeError as exc:
            if args.layout == "catalog" and "CATALOG" in str(exc).upper():
                raise SystemExit(f"{exc}\n\nCREATE CATALOG failed. Re-run with --layout schema and set layout=schema in the bundle.")
            raise
    print("done")


if __name__ == "__main__":
    main()
