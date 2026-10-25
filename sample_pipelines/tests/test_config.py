"""Pure-Python tests (no Spark needed)."""

from common.config import parse_args, tables_from_args


def test_catalog_layout_names():
    t = tables_from_args(parse_args(["--catalog", "prod", "--env", "prod"]))
    assert t.bronze_orders == "prod.bronze.orders"
    assert t.silver_orders == "prod.silver.orders"
    assert t.gold_daily_sales == "prod.gold.daily_sales"
    assert t.landing_path == "/Volumes/prod/bronze/landing/orders"


def test_schema_layout_names():
    t = tables_from_args(parse_args(["--catalog", "workspace", "--env", "qa", "--layout", "schema"]))
    assert t.bronze_orders == "workspace.qa_bronze.orders"
    assert t.landing_path == "/Volumes/workspace/qa_bronze/landing/orders"


def test_catalog_comes_from_arguments():
    for env in ("dev", "qa", "prod"):
        t = tables_from_args(parse_args(["--catalog", env, "--env", env]))
        assert t.catalog == env
