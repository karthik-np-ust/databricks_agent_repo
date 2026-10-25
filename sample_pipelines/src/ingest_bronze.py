"""Bronze: load every CSV in the landing volume as strings, untouched."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # makes `common` importable on Databricks

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common.config import parse_args, tables_from_args


def read_landing(spark: SparkSession, path: str) -> DataFrame:
    # No schema inference: bronze keeps the raw strings, typing happens in silver.
    return (
        spark.read.option("header", True).option("inferSchema", False).csv(path)
        .withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_source_file", F.col("_metadata.file_path"))
    )


def main() -> None:
    args = parse_args()
    tables = tables_from_args(args)
    spark = SparkSession.builder.getOrCreate()
    df = read_landing(spark, tables.landing_path)
    df.write.mode("overwrite").option("overwriteSchema", True).saveAsTable(tables.bronze_orders)
    print(f"bronze: wrote {df.count()} rows to {tables.bronze_orders}")


if __name__ == "__main__":
    main()
