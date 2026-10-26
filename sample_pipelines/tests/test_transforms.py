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


def test_clean_orders_matches_silver_schema_contract(spark, fixtures_dir):
    from common.schemas import SILVER_SCHEMA

    out = clean_orders(read_fixture(spark, fixtures_dir, "orders_clean.csv"))
    assert [(f.name, f.dataType) for f in out.schema] == [(f.name, f.dataType) for f in SILVER_SCHEMA]


def test_top_stores_orders_by_revenue(spark, fixtures_dir):
    from sales_report import top_stores

    report = top_stores(daily_sales(clean_orders(read_fixture(spark, fixtures_dir, "orders_clean.csv")))).collect()
    assert [r["store_id"] for r in report] == ["S002", "S001", "S003"]
    assert float(report[0]["revenue"]) == 50.0


def test_clean_orders_handles_malformed_qty_and_price(spark):
    """Malformed numeric strings become NULL via try_cast instead of failing the job."""
    from pyspark.sql import Row

    # One clean row, one with 'abc' qty, one with 'xyz' unit_price
    data = [
        Row(order_id="ORD-1", order_ts="2026-10-01 09:00:00", store_id="S001", product_id="P1", qty="1", unit_price="10.00", updated_at="2026-10-01 09:05:00", notes=""),
        Row(order_id="ORD-2", order_ts="2026-10-01 10:00:00", store_id="S001", product_id="P2", qty="abc", unit_price="7.50", updated_at="2026-10-01 10:05:00", notes=""),
        Row(order_id="ORD-3", order_ts="2026-10-01 11:00:00", store_id="S002", product_id="P3", qty="2", unit_price="xyz", updated_at="2026-10-01 11:05:00", notes=""),
    ]
    df = spark.createDataFrame(data)
    out = clean_orders(df)
    rows = {r["order_id"]: r for r in out.collect()}
    # Clean row parses correctly
    assert rows["ORD-1"]["qty"] == 1
    assert float(rows["ORD-1"]["unit_price"]) == 10.0
    # Malformed qty becomes NULL
    assert rows["ORD-2"]["qty"] is None
    assert float(rows["ORD-2"]["unit_price"]) == 7.5
    # Malformed unit_price becomes NULL
    assert rows["ORD-3"]["qty"] == 2
    assert rows["ORD-3"]["unit_price"] is None
    # All three rows are present (not dropped)
    assert out.count() == 3
