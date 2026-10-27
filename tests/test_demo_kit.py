"""The fault kit's release-fault mechanics (the faults themselves live in the pipelines repo)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inject_fault import FAULTS, apply_edits  # noqa: E402

NOTEBOOK = '''# Databricks notebook source
from pyspark.sql import functions as F
from databricks.sdk.runtime import dbutils

catalog = dbutils.widgets.get("catalog")

# COMMAND ----------

for key_column in ["order_id", "store_id"]:
    null_keys = 0
    if null_keys:
        raise Exception(f"DQAssertionError: {null_keys} row(s) with null {key_column}")

price = F.col("unit_price").cast("decimal(10,2)")
'''


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "notebooks").mkdir()
    (tmp_path / "notebooks" / "transform_silver.py").write_text(NOTEBOOK, encoding="utf-8")
    return tmp_path


def test_apply_edits_replaces_first_occurrence(repo):
    edits = [{"file": "notebooks/transform_silver.py", "find": 'F.col("unit_price").cast', "replace": 'F.col("unit_prce").cast'}]
    assert apply_edits(repo, edits) == ["notebooks/transform_silver.py"]
    assert 'F.col("unit_prce").cast' in (repo / "notebooks/transform_silver.py").read_text(encoding="utf-8")


def test_apply_edits_writes_nothing_when_a_target_is_missing(repo):
    edits = [
        {"file": "notebooks/transform_silver.py", "find": 'catalog = dbutils.widgets.get("catalog")', "replace": 'catalog = "x"'},
        {"file": "notebooks/transform_silver.py", "find": "not in the file", "replace": "y"},
    ]
    with pytest.raises(ValueError):
        apply_edits(repo, edits)
    assert (repo / "notebooks/transform_silver.py").read_text(encoding="utf-8") == NOTEBOOK


def test_syntax_error_edit_breaks_a_notebook(repo):
    apply_edits(repo, [{"file": "notebooks/transform_silver.py", "find": "    if null_keys:", "replace": "    if null_keys"}])
    with pytest.raises(SyntaxError):
        compile((repo / "notebooks/transform_silver.py").read_text(encoding="utf-8"), "nb", "exec")


def test_inject_fault_catalog_numbers_cover_1_to_14():
    assert sorted(f.number for f in FAULTS.values()) == list(range(1, 15))
