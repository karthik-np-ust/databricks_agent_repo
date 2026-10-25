"""Sales report: top stores by revenue. ``--sleep-seconds`` simulates a slow run (fault scenario 1)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # makes `common` importable on Databricks

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from common.config import parse_args, tables_from_args


def main() -> None:
    args = parse_args(extra=lambda p: p.add_argument("--sleep-seconds", type=int, default=0))
    tables = tables_from_args(args)
    spark = SparkSession.builder.getOrCreate()
    report = (
        spark.table(tables.gold_daily_sales)
        .groupBy("store_id")
        .agg(F.sum("revenue").alias("revenue"), F.sum("orders").alias("orders"))
        .orderBy(F.desc("revenue"))
    )
    report.write.mode("overwrite").option("overwriteSchema", True).saveAsTable(tables.gold_sales_report)
    if args.sleep_seconds > 0:
        print(f"sales_report: simulating slow work for {args.sleep_seconds}s")
        time.sleep(args.sleep_seconds)
    print(f"sales_report: wrote {report.count()} rows to {tables.gold_sales_report}")


if __name__ == "__main__":
    main()
