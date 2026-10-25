"""Column contract for the orders feed."""

BRONZE_COLUMNS = ["order_id", "order_ts", "store_id", "product_id", "qty", "unit_price", "updated_at", "notes"]

SILVER_DDL = (
    "order_id STRING, order_ts TIMESTAMP, store_id STRING, product_id STRING, "
    "qty INT, unit_price DECIMAL(10,2), updated_at TIMESTAMP"
)
