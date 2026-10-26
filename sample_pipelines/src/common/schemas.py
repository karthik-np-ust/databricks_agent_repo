"""Column contract for the orders feed, as PySpark schemas."""

from pyspark.sql.types import DecimalType, IntegerType, StringType, StructField, StructType, TimestampType

# Bronze keeps the raw CSV as strings, read by header name (no fixed schema), so an
# upstream rename surfaces as an error in silver instead of being mapped by position.
BRONZE_COLUMNS = ["order_id", "order_ts", "store_id", "product_id", "qty", "unit_price", "updated_at", "notes"]

SILVER_SCHEMA = StructType(
    [
        StructField("order_id", StringType()),
        StructField("order_ts", TimestampType()),
        StructField("store_id", StringType()),
        StructField("product_id", StringType()),
        StructField("qty", IntegerType()),
        StructField("unit_price", DecimalType(10, 2)),
        StructField("updated_at", TimestampType()),
    ]
)

PARTNER_FEED_SCHEMA = StructType(
    [
        StructField("store_id", StringType()),
        StructField("footfall", IntegerType()),
        StructField("loaded_at", StringType()),
    ]
)
