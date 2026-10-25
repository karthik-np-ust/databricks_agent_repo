"""Data-quality checks. A failed check stops the pipeline."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


class DQAssertionError(Exception):
    pass


def assert_not_null(df: DataFrame, columns: list[str]) -> None:
    for column in columns:
        bad = df.filter(F.col(column).isNull()).count()
        if bad:
            raise DQAssertionError(f"DQAssertionError: {bad} row(s) with null {column}")
