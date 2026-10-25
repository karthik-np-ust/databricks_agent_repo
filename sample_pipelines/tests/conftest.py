import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(scope="session")
def spark():
    """Local Spark with ANSI mode on, like Databricks serverless. Skipped without pyspark + Java."""
    pyspark = pytest.importorskip("pyspark")
    if not (os.environ.get("JAVA_HOME") or shutil.which("java")):
        pytest.skip("Java is not installed; Spark tests run in CI")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("sample-pipelines-tests")
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.fixture
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
