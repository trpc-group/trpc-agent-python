# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for agent evaluator (agent_evaluator)."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalSetAggregateResult
from trpc_agent_sdk.evaluation import EvaluateResult
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import PassNC


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
