"""Spark tests for the transforms (run in CI; skipped locally without Java)."""

import os

import pytest

pytest.importorskip("pyspark")

from build_gold import daily_sales  # noqa: E402
from transform_silver import clean_orders  # noqa: E402


def read_fixture(spark, fixtures_dir, name):
    return spark.read.option("header", True).option("inferSchema", False).csv(os.path.join(fixtures_dir, name))


def test_clean_orders_types_columns(spark, fixtures_dir):
    out = clean_orders(read_fixture(spark, fixtures_dir, "orders_clean.csv"))
    types = dict(out.dtypes)
    assert types["qty"] == "int"
    assert types["unit_price"] == "decimal(10,2)"
    assert types["order_ts"] == "timestamp"
    assert out.count() == 6


def test_daily_sales_aggregates(spark, fixtures_dir):
    gold = daily_sales(clean_orders(read_fixture(spark, fixtures_dir, "orders_clean.csv")))
    rows = {r["store_id"]: r for r in gold.collect()}
    assert rows["S001"]["units"] == 3
    assert rows["S001"]["orders"] == 2
    assert float(rows["S001"]["revenue"]) == 25.0


def test_clean_orders_handles_bad_qty(spark, fixtures_dir):
    """clean_orders should return NULL for non-numeric qty instead of throwing."""
    df = read_fixture(spark, fixtures_dir, "orders_bad_qty.csv")
    out = clean_orders(df)
    # Should have 5 rows (all input rows preserved)
    assert out.count() == 5
    # Rows with bad qty should have NULL qty
    bad_rows = out.filter(F.col("qty").isNull()).collect()
    assert len(bad_rows) == 2
    bad_order_ids = {r["order_id"] for r in bad_rows}
    assert bad_order_ids == {"ORD-000002", "ORD-000004"}
    # Good rows should have correct int values
    good_rows = out.filter(F.col("qty").isNotNull()).collect()
    assert len(good_rows) == 3
    qty_map = {r["order_id"]: r["qty"] for r in good_rows}
    assert qty_map["ORD-000001"] == 1
    assert qty_map["ORD-000003"] == 2
    assert qty_map["ORD-000005"] == 3
