"""Silver: type the bronze orders, check quality and MERGE into silver.orders."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # makes `common` importable on Databricks

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common.config import parse_args, tables_from_args
from common.dq import assert_not_null
from common.schemas import SILVER_DDL


def clean_orders(df: DataFrame) -> DataFrame:
    assert_not_null(df, ["order_id", "store_id"])
    return df.select(
        F.col("order_id"),
        F.to_timestamp("order_ts").alias("order_ts"),
        F.col("store_id"),
        F.col("product_id"),
        F.col("qty").cast("int").alias("qty"),
        F.col("unit_price").cast("decimal(10,2)").alias("unit_price"),
        F.to_timestamp("updated_at").alias("updated_at"),
    )


def merge_into_silver(spark: SparkSession, df: DataFrame, table: str) -> None:
    spark.sql(f"CREATE TABLE IF NOT EXISTS {table} ({SILVER_DDL})")
    df.createOrReplaceTempView("orders_updates")
    spark.sql(
        f"""
        MERGE INTO {table} AS t
        USING orders_updates AS s
        ON t.order_id = s.order_id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def main() -> None:
    args = parse_args()
    tables = tables_from_args(args)
    spark = SparkSession.builder.getOrCreate()
    cleaned = clean_orders(spark.table(tables.bronze_orders))
    merge_into_silver(spark, cleaned, tables.silver_orders)
    print(f"silver: merged into {tables.silver_orders}")


if __name__ == "__main__":
    main()
