"""Silver: type the bronze orders, check quality and upsert into silver.orders (Delta MERGE)."""

import os
import sys

# Make `common` importable. Serverless spark_python_task has no __file__, so the
# job passes the deployed src folder as --src-dir (see resources/jobs.yml).
_SRC = sys.argv[sys.argv.index("--src-dir") + 1] if "--src-dir" in sys.argv else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common.config import parse_args, tables_from_args
from common.dq import assert_not_null
from common.schemas import SILVER_SCHEMA


def clean_orders(df: DataFrame) -> DataFrame:
    assert_not_null(df, ["order_id", "store_id"])
    return df.select(
        F.col("order_id"),
        F.to_timestamp("order_ts").alias("order_ts"),
        F.col("store_id"),
        F.col("product_id"),
        F.expr("try_cast(qty AS INT)").alias("qty"),
        F.expr("try_cast(unit_price AS DECIMAL(10,2))").alias("unit_price"),
        F.to_timestamp("updated_at").alias("updated_at"),
    )


def merge_into_silver(spark: SparkSession, df: DataFrame, table: str) -> None:
    """Upsert by order_id with the Delta Lake Python API."""
    # Imported here: Databricks provides delta, local unit tests (plain PySpark) don't need it.
    from delta.tables import DeltaTable

    DeltaTable.createIfNotExists(spark).tableName(table).addColumns(SILVER_SCHEMA).execute()
    (
        DeltaTable.forName(spark, table).alias("t")
        .merge(df.alias("s"), "t.order_id = s.order_id")
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )


def main() -> None:
    args = parse_args()
    tables = tables_from_args(args)
    spark = SparkSession.builder.getOrCreate()
    cleaned = clean_orders(spark.read.table(tables.bronze_orders))
    merge_into_silver(spark, cleaned, tables.silver_orders)
    print(f"silver: merged into {tables.silver_orders}")


if __name__ == "__main__":
    main()
