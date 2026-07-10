# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for agent evaluator (agent_evaluator)."""

import json

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import _agent_evaluator as agent_evaluator_module
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalSetAggregateResult
from trpc_agent_sdk.evaluation import EvaluateResult
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import PassNC


def test_evaluation_cases_failed_is_public_with_private_alias():
    from trpc_agent_sdk import evaluation
    from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed

    assert evaluation.EvaluationCasesFailed is _EvaluationCasesFailed
    assert issubclass(evaluation.EvaluationCasesFailed, AssertionError)


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        (r"C:\x\a.evalset.json", (r"C:\x\a.evalset.json", None)),
        (
            r"C:\x\a.evalset.json:case-1",
            (r"C:\x\a.evalset.json", "case-1"),
        ),
        (
            "/tmp/a.evalset.json:case-1",
            ("/tmp/a.evalset.json", "case-1"),
        ),
    ],
)
def test_split_eval_set_selector_is_drive_safe(raw_path, expected):
    assert agent_evaluator_module._split_eval_set_selector(raw_path) == expected


def test_load_eval_set_from_absolute_path_and_selector(tmp_path):
    evalset_path = (tmp_path / "sample.evalset.json").resolve()
    evalset_path.write_text(
        json.dumps({
            "eval_set_id": "set",
            "eval_cases": [{
                "eval_id": "case-1",
                "conversation": [{
                    "invocation_id": "turn-1",
                    "user_content": {
                        "role": "user",
                        "parts": [{"text": "query"}],
                    },
                    "final_response": {
                        "role": "model",
                        "parts": [{"text": "expected"}],
                    },
                }],
            }],
        }),
        encoding="utf-8",
    )

    loaded = AgentEvaluator._load_eval_set_from_file(
        str(evalset_path),
        agent_evaluator_module.EvalConfig(criteria={}),
    )
    selected = AgentEvaluator._load_eval_set_from_file(
        f"{evalset_path}:case-1",
        agent_evaluator_module.EvalConfig(criteria={}),
    )

    assert [case.eval_id for case in loaded.eval_cases] == ["case-1"]
    assert [case.eval_id for case in selected.eval_cases] == ["case-1"]


class TestPassNC:
    """Test suite for PassNC dataclass."""

    def test_pass_nc_fields(self):
        """Test PassNC n and c."""
        p = PassNC(n=5, c=3)
        assert p.n == 5
        assert p.c == 3

    def test_pass_nc_frozen(self):
        """Test PassNC is frozen."""
        p = PassNC(n=1, c=1)
        with pytest.raises(Exception):
            p.n = 2


class TestAgentEvaluatorParsePassNc:
    """Test suite for AgentEvaluator.parse_pass_nc."""

    def test_parse_pass_nc_empty_result(self):
        """Test parse_pass_nc with empty EvaluateResult."""
        result = EvaluateResult(results_by_eval_set_id={})
        out = AgentEvaluator.parse_pass_nc(result)
        assert out == {}

    def test_parse_pass_nc_single_set(self):
        """Test parse_pass_nc with one eval set, two cases, one run each."""
        emr = EvalMetricResult(
            metric_name="m1",
            threshold=0.5,
            score=1.0,
            eval_status=EvalStatus.PASSED,
        )
        ecr_passed = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_001",
            final_eval_status=EvalStatus.PASSED,
            overall_eval_metric_results=[emr],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        ecr_failed = EvalCaseResult(
            eval_set_id="set1",
            eval_id="case_002",
            final_eval_status=EvalStatus.FAILED,
            overall_eval_metric_results=[
                EvalMetricResult(
                    metric_name="m1",
                    threshold=0.5,
                    score=0.0,
                    eval_status=EvalStatus.FAILED,
                ),
            ],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )
        set_result = EvalSetAggregateResult(
            eval_results_by_eval_id={
                "case_001": [ecr_passed],
                "case_002": [ecr_failed],
            },
            num_runs=1,
        )
        result = EvaluateResult(
            results_by_eval_set_id={"set1": set_result},
        )
        out = AgentEvaluator.parse_pass_nc(result)
        assert "set1" in out
        assert out["set1"].n == 1  # num_runs
        assert out["set1"].c == 0  # no run where every case passed

    def test_pass_at_k_delegates(self):
        """Test AgentEvaluator.pass_at_k delegates to _eval_pass."""
        assert AgentEvaluator.pass_at_k(10, 5, 3) >= 0
        assert AgentEvaluator.pass_at_k(10, 5, 3) <= 1

    def test_pass_hat_k_delegates(self):
        """Test AgentEvaluator.pass_hat_k delegates to _eval_pass."""
        assert AgentEvaluator.pass_hat_k(10, 5, 2) == 0.25
