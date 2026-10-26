"""Partner feed: simulated external API pull.

Fault switches:
  --flaky-rate 0.7         fails like a flaky API (scenario 2, transient)
  --leak-fake-token true   prints a FAKE token-like string, then fails (scenario 13, redaction)
  secret scope partner-<env>/api_key missing -> "Secret does not exist" (scenario 11, infra)
"""

import os
import random
import sys
from datetime import datetime, timezone

# Make `common` importable. Serverless spark_python_task has no __file__, so the
# job passes the deployed src folder as --src-dir (see resources/jobs.yml).
_SRC = sys.argv[sys.argv.index("--src-dir") + 1] if "--src-dir" in sys.argv else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)

from pyspark.sql import SparkSession

from common.config import parse_args, tables_from_args
from common.schemas import PARTNER_FEED_SCHEMA

# Obviously fake, but shaped like a Databricks token so the agent's redaction must catch it.
FAKE_TOKEN = "dapiFAKE00000000000000000000000000"


def get_api_key(env: str) -> str:
    from databricks.sdk.runtime import dbutils

    return dbutils.secrets.get(scope=f"partner-{env}", key="api_key")


def fetch_partner_rows(flaky_rate: float) -> list[tuple[str, int]]:
    if random.random() < flaky_rate:
        raise ConnectionError("Connection reset by peer while calling partner API (HTTP 503)")
    return [(f"S{n:03d}", random.randint(0, 500)) for n in range(1, 11)]


def main() -> None:
    def extra(p):
        p.add_argument("--flaky-rate", type=float, default=0.0)
        p.add_argument("--leak-fake-token", default="false")

    args = parse_args(extra=extra)
    tables = tables_from_args(args)
    get_api_key(args.env)  # proves the secret exists; the value is never printed

    if args.leak_fake_token.lower() == "true":
        print(f"DEBUG calling partner API with Authorization: Bearer {FAKE_TOKEN}")
        raise RuntimeError(f"Partner API rejected credentials for token {FAKE_TOKEN}")

    rows = fetch_partner_rows(args.flaky_rate)
    spark = SparkSession.builder.getOrCreate()
    loaded_at = datetime.now(timezone.utc).isoformat()
    df = spark.createDataFrame([(s, v, loaded_at) for s, v in rows], schema=PARTNER_FEED_SCHEMA)
    df.write.format("delta").mode("overwrite").saveAsTable(tables.partner_feed)
    print(f"partner_feed: wrote {len(rows)} rows to {tables.partner_feed}")


if __name__ == "__main__":
    main()
