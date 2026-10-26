"""Gold: daily sales per store."""

import os
import sys

# Make `common` importable. Serverless spark_python_task has no __file__, so the
# job passes the deployed src folder as --src-dir (see resources/jobs.yml).
_SRC = sys.argv[sys.argv.index("--src-dir") + 1] if "--src-dir" in sys.argv else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common.config import parse_args, tables_from_args


def daily_sales(df: DataFrame) -> DataFrame:
    return (
        df.groupBy(F.to_date("order_ts").alias("order_date"), "store_id")
        .agg(
            F.sum(F.col("qty") * F.col("unit_price")).alias("revenue"),
            F.sum("qty").alias("units"),
            F.count("*").alias("orders"),
        )
        .withColumn("avg_unit_price", F.col("revenue") / F.col("units"))
    )


def main() -> None:
    args = parse_args()
    tables = tables_from_args(args)
    spark = SparkSession.builder.getOrCreate()
    result = daily_sales(spark.table(tables.silver_orders))
    result.write.mode("overwrite").option("overwriteSchema", True).saveAsTable(tables.gold_daily_sales)
    print(f"gold: wrote {result.count()} rows to {tables.gold_daily_sales}")


if __name__ == "__main__":
    main()
