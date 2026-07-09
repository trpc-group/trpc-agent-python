"""Shared fixtures and utilities for all pipeline tests."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure imports work from the example directory
_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.config import PipelineConfig, load_pipeline_config
from pipeline.baseline import BaselineResult


@pytest.fixture
def data_dir():
    """Path to the data/ directory with evalsets and config."""
    return _parent / "data"


@pytest.fixture
def pipeline_config():
    """Default fake-mode pipeline configuration."""
    return load_pipeline_config(mode="fake", verbose=False)


@pytest.fixture
def sample_baseline():
    """Baseline result with 6 cases, 3 failed."""
    return BaselineResult(
        evalset_id="test-evalset",
        pass_rate=0.5,
        total_cases=6,
        passed_cases=3,
        failed_cases=3,
        failed_case_ids=["case_001", "case_002", "case_003"],
        metric_breakdown={"overall_pass_rate": 0.5},
        per_case_results=[
            {"eval_id": "case_001", "pass": False, "reason": "tool_call_error: wrong parameter"},
            {"eval_id": "case_002", "pass": False, "reason": "final_response_mismatch"},
            {"eval_id": "case_003", "pass": False, "reason": "llm_rubric_not_met: quality score below threshold"},
            {"eval_id": "case_004", "pass": True, "reason": ""},
            {"eval_id": "case_005", "pass": True, "reason": "tool_call_error but passed"},
            {"eval_id": "case_006", "pass": True, "reason": ""},
        ],
    )


@pytest.fixture
def all_pass_baseline():
    """Baseline result with all cases passed."""
    return BaselineResult(
        evalset_id="all-pass-evalset",
        pass_rate=1.0,
        total_cases=3,
        passed_cases=3,
        failed_cases=0,
        failed_case_ids=[],
        metric_breakdown={"overall_pass_rate": 1.0},
        per_case_results=[
            {"eval_id": "c1", "pass": True, "reason": ""},
            {"eval_id": "c2", "pass": True, "reason": ""},
            {"eval_id": "c3", "pass": True, "reason": ""},
        ],
    )


@pytest.fixture
def all_fail_baseline():
    """Baseline result with all cases failed."""
    return BaselineResult(
        evalset_id="all-fail-evalset",
        pass_rate=0.0,
        total_cases=4,
        passed_cases=0,
        failed_cases=4,
        failed_case_ids=["f1", "f2", "f3", "f4"],
        metric_breakdown={"overall_pass_rate": 0.0},
        per_case_results=[
            {"eval_id": "f1", "pass": False, "reason": "tool_parameter_error: missing required arg"},
            {"eval_id": "f2", "pass": False, "reason": "wrong_tool_selected: used add instead of multiply"},
            {"eval_id": "f3", "pass": False, "reason": "knowledge_recall_insufficient: formula not found"},
            {"eval_id": "f4", "pass": False, "reason": "format_not_as_required: expected JSON got plain text"},
        ],
    )


@pytest.fixture
def temp_evalset():
    """Create a temporary evalset JSON file."""
    def _create(cases: list[dict], evalset_id: str = "temp-evalset") -> str:
        data = {
            "eval_set_id": evalset_id,
            "name": "Temporary Evalset",
            "eval_cases": cases,
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            return f.name
    return _create


@pytest.fixture
def temp_json_file():
    """Create a temporary JSON file with given content."""
    def _create(data: dict) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            return f.name
    return _create


def cleanup_temp(*paths: str) -> None:
    """Clean up temporary files."""
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass
