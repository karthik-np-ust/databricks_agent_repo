"""Silver: type the bronze orders, check quality and MERGE into silver.orders."""

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
from common.schemas import SILVER_DDL


def clean_orders(df: DataFrame) -> DataFrame:
    assert_not_null(df, ["order_id", "store_id"])
    # Use try_cast to tolerate malformed numeric strings (e.g. 'abc') -> NULL
    # Spark 3.4+ provides try_cast; fallback to when/otherwise for older versions.
    qty_cast = F.try_cast(F.col("qty"), "int")
    return df.select(
        F.col("order_id"),
        F.to_timestamp("order_ts").alias("order_ts"),
        F.col("store_id"),
        F.col("product_id"),
        qty_cast.alias("qty"),
        F.col("unit_price").cast("decimal(10,2)").alias("unit_price"),
        F.to_timestamp("updated_at").alias("updated_at"),
    )


def split_valid_invalid(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split rows where qty cast succeeded vs failed (original qty not null but cast is null)."""
    # Original qty is string; cast failed if original is non-null and cast result is null
    valid = df.filter(F.col("qty").isNotNull())
    invalid = df.filter(F.col("qty").isNull() & F.col("qty").isNotNull())
    # The above condition is always false; need to reference original column.
    # Since we lost original qty, re-read or pass original. Simpler: do split before select.
    # This function is kept for clarity but not used; logic moved into main.
    return valid, invalid


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
    
    bronze = spark.table(tables.bronze_orders)
    
    # Cast qty safely, keeping original for quarantine logic
    cleaned = bronze.select(
        F.col("order_id"),
        F.to_timestamp("order_ts").alias("order_ts"),
        F.col("store_id"),
        F.col("product_id"),
        F.col("qty").alias("qty_raw"),
        F.try_cast(F.col("qty"), "int").alias("qty"),
        F.col("unit_price").cast("decimal(10,2)").alias("unit_price"),
        F.to_timestamp("updated_at").alias("updated_at"),
    )
    
    # Data quality: order_id and store_id must not be null
    assert_not_null(cleaned, ["order_id", "store_id"])
    
    # Split valid (qty cast ok) and invalid (qty_raw not null but qty is null)
    valid = cleaned.filter(F.col("qty").isNotNull()).drop("qty_raw")
    invalid = cleaned.filter(F.col("qty_raw").isNotNull() & F.col("qty").isNull()).drop("qty")
    
    # Quarantine invalid rows
    if invalid.count() > 0:
        quarantine_table = f"{tables.catalog}.{tables.env}_bronze.orders_quarantine" if tables.layout == "schema" else f"{tables.catalog}.bronze.orders_quarantine"
        spark.sql(f"CREATE TABLE IF NOT EXISTS {quarantine_table} (order_id STRING, order_ts STRING, store_id STRING, product_id STRING, qty_raw STRING, unit_price STRING, updated_at STRING, _ingested_at TIMESTAMP, _source_file STRING)")
        invalid.write.mode("append").saveAsTable(quarantine_table)
        print(f"silver: quarantined {invalid.count()} rows with invalid qty to {quarantine_table}")
    
    # Merge only valid rows
    merge_into_silver(spark, valid, tables.silver_orders)
    print(f"silver: merged {valid.count()} rows into {tables.silver_orders}")


if __name__ == "__main__":
    main()
