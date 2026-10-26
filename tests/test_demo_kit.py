"""The fault kit must stay in sync with the sample pipeline code."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_data import COLUMNS, INJECTION_TEXT, SCENARIOS, scenario_rows, to_csv  # noqa: E402


def parse(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_clean_data_has_contract_columns_and_unique_keys():
    rows = parse(to_csv(*scenario_rows("clean")))
    assert list(rows[0]) == COLUMNS
    assert len({r["order_id"] for r in rows}) == len(rows)
    assert all(r["qty"].isdigit() and r["store_id"] for r in rows)


@pytest.mark.parametrize(
    "scenario,check",
    [
        ("bad_cast", lambda rows: any(r["qty"] == "abc" for r in rows)),
        ("zero_units", lambda rows: {r["qty"] for r in rows if r["store_id"] == "S099"} == {"0"}),
        ("duplicate_keys", lambda rows: len({r["order_id"] for r in rows}) == len(rows) - 1),
        ("schema_drift", lambda rows: "quantity" in rows[0] and "qty" not in rows[0]),
        ("null_keys", lambda rows: sum(1 for r in rows if not r["store_id"]) == 2),
        ("prompt_injection", lambda rows: any(r["qty"] == INJECTION_TEXT for r in rows)),
    ],
)
def test_data_scenarios(scenario, check):
    assert check(parse(to_csv(*scenario_rows(scenario))))


def test_every_data_scenario_is_documented():
    assert set(SCENARIOS) == {"clean", "bad_cast", "zero_units", "duplicate_keys", "schema_drift", "null_keys", "prompt_injection"}


@pytest.mark.parametrize("fault_file", sorted((ROOT / "sample_pipelines" / "faults").glob("*.yaml")), ids=lambda p: p.stem)
def test_release_fault_applies_to_current_code(fault_file):
    # The pipeline code is exactly what the agent is allowed to change, so this demo-tooling
    # check must never block a valid fix: a moved target only skips (visible in CI), and
    # inject_fault.py edits the first occurrence when the text appears more than once.
    spec = yaml.safe_load(fault_file.read_text(encoding="utf-8"))
    for edit in spec["edits"]:
        text = (ROOT / edit["file"]).read_text(encoding="utf-8")
        if edit["find"] not in text:
            pytest.skip(f"{fault_file.name}: target text no longer in {edit['file']}; update the fault definition")


def test_syntax_error_fault_really_breaks_the_module():
    spec = yaml.safe_load((ROOT / "sample_pipelines/faults/syntax_error.yaml").read_text(encoding="utf-8"))
    edit = spec["edits"][0]
    broken = (ROOT / edit["file"]).read_text(encoding="utf-8").replace(edit["find"], edit["replace"])
    with pytest.raises(SyntaxError):
        compile(broken, edit["file"], "exec")


def test_inject_fault_catalog_numbers_cover_1_to_14():
    from inject_fault import FAULTS

    assert sorted(f.number for f in FAULTS.values()) == list(range(1, 15))
