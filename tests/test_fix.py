"""Patch guards of the Recovery fix writer (the code-fix graph itself: tests/test_code_fix.py)."""

from __future__ import annotations

import pytest

from pipeline_agent.config import FixConfig
from pipeline_agent.fix import CodeContext, FixProposal, validate_proposal

SRC = "sample_pipelines/src/transform_silver.py"
TEST = "sample_pipelines/tests/test_transforms.py"
_IMPORTS = "from pyspark.sql import functions as F\nfrom common.dq import assert_not_null\n"
ORIGINAL_SRC = _IMPORTS + '\ndef clean(df):\n    assert_not_null(df, ["order_id"])\n    return df.select(F.col("qty").cast("int"))\n'
FIXED_SRC = _IMPORTS + '\ndef clean(df):\n    assert_not_null(df, ["order_id"])\n    return df.select(F.expr("try_cast(qty AS INT)"))\n'
ORIGINAL_TEST = "def test_ok():\n    assert True\n"
FIXED_TEST = ORIGINAL_TEST + "\ndef test_bad_qty_is_null():\n    assert True\n"

CFG = FixConfig(
    repo="o/r",
    required_checks=["agent-tests"],
    allowed_paths=["sample_pipelines/src/", "sample_pipelines/tests/"],
    protected_patterns=["assert_not_null("],
)


def proposal(files=None):
    return {
        "summary": "Use try_cast for qty",
        "files": files or [{"path": SRC, "content": FIXED_SRC}, {"path": TEST, "content": FIXED_TEST}],
        "risk": "low",
        "test_plan": "bad qty becomes null",
    }


# validation -------------------------------------------------------------------------
CTX = CodeContext(files={SRC: ORIGINAL_SRC, TEST: ORIGINAL_TEST}, allowed_paths=CFG.allowed_paths)


def errors_for(files):
    return validate_proposal(FixProposal.model_validate(proposal(files)), CTX, CFG)


def test_valid_fix_passes():
    assert errors_for(None) == []


@pytest.mark.parametrize(
    "files,expected",
    [
        ([{"path": ".github/workflows/deploy.yml", "content": "x"}, {"path": TEST, "content": FIXED_TEST}], "outside allowed paths"),
        ([{"path": "sample_pipelines/src/../../config/x.py", "content": "x=1"}, {"path": TEST, "content": FIXED_TEST}], "invalid path"),
        ([{"path": SRC, "content": "def broken(:\n"}, {"path": TEST, "content": FIXED_TEST}], "does not compile"),
        ([{"path": SRC, "content": "def clean(df):\n    return df\n"}, {"path": TEST, "content": FIXED_TEST}], "removes protected check"),
        ([{"path": SRC, "content": FIXED_SRC}], "no test added"),
        ([{"path": TEST, "content": FIXED_TEST}], "no source file changed"),
        ([{"path": SRC, "content": ORIGINAL_SRC}, {"path": TEST, "content": ORIGINAL_TEST}], "no file actually changes"),
        ([{"path": SRC, "content": FIXED_SRC + "\nTOKEN = 'dapiTESTnotARealTokenXXXXXXXXXXXXXX'\n"}, {"path": TEST, "content": FIXED_TEST}], "secret-like"),
    ],
)
def test_guards_reject_bad_fixes(files, expected):
    assert any(expected in e for e in errors_for(files)), errors_for(files)


def test_guard_catches_undefined_name_like_missing_F_import():
    # Real case 2026-10-26: the agent's test used F without importing it.
    test = ORIGINAL_TEST + "\ndef test_bad():\n    assert F.col('qty') is not None\n"
    errors = errors_for([{"path": SRC, "content": FIXED_SRC}, {"path": TEST, "content": test}])
    assert any("undefined name 'F'" in e for e in errors), errors


def test_forbidden_pattern_rejected_with_hint():
    from pipeline_agent.config import ForbiddenPattern

    cfg = CFG.model_copy(update={"forbidden_patterns": [ForbiddenPattern(pattern=r"\bF\.try_cast\s*\(", message="use F.expr")]})
    bad = FIXED_SRC.replace('F.expr("try_cast(qty AS INT)")', 'F.try_cast(F.col("qty"), "int")')
    p = FixProposal.model_validate(proposal([{"path": SRC, "content": bad}, {"path": TEST, "content": FIXED_TEST}]))
    errors = validate_proposal(p, CTX, cfg)
    assert any("use F.expr" in e and "line" in e for e in errors), errors
    assert validate_proposal(FixProposal.model_validate(proposal()), CTX, cfg) == [], "F.expr('try_cast(...)') is fine"


def test_repo_fix_config_forbids_try_cast():
    from pathlib import Path

    from pipeline_agent.config import load_agent_config

    # The repo's forbidden_patterns, with this module's fixture paths allowed.
    cfg = load_agent_config(Path(__file__).parents[1] / "config").fix.model_copy(update={"allowed_paths": CFG.allowed_paths})
    bad = FIXED_SRC.replace('F.expr("try_cast(qty AS INT)")', 'F.col("qty").try_cast("int")')
    p = FixProposal.model_validate(proposal([{"path": SRC, "content": bad}, {"path": TEST, "content": FIXED_TEST}]))
    assert any("PySpark 4.0" in e for e in validate_proposal(p, CTX, cfg))
