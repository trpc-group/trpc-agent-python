# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for agent evaluator (agent_evaluator)."""

import os

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import EvalSetAggregateResult
from trpc_agent_sdk.evaluation import EvaluateResult
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import PassNC
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


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


class TestLoadEvalSetFromFile:
    """Test suite for AgentEvaluator._load_eval_set_from_file.

    Covers the case-selector colon parsing.  The drive-letter guard
    ("an existing path containing ':' must load as-is") is exercised on
    Windows by the absolute ``tmp_path`` ("C:\\...") and on POSIX by a
    file whose name literally contains a colon.
    """

    @staticmethod
    def _write_eval_set(tmp_path, case_ids):
        """Write a minimal eval set file and return its absolute path."""
        cases = [
            EvalCase(
                eval_id=case_id,
                conversation=[
                    Invocation(
                        invocation_id="i",
                        user_content=Content(parts=[Part(text="hi")]),
                    ),
                ],
            )
            for case_id in case_ids
        ]
        eval_set = EvalSet(eval_set_id="set1", eval_cases=cases)
        file_path = tmp_path / "set.evalset.json"
        file_path.write_text(eval_set.model_dump_json(), encoding="utf-8")
        return str(file_path)

    def test_load_absolute_path_without_selector(self, tmp_path):
        """An existing absolute path must load as-is.

        On Windows ``tmp_path`` starts with a drive letter, so the ':' guard
        is hit here; POSIX coverage of the same guard lives in
        ``test_load_existing_path_containing_colon``.
        """
        file_path = self._write_eval_set(tmp_path, ["case_a", "case_b"])
        eval_set = AgentEvaluator._load_eval_set_from_file(file_path, EvalConfig(criteria={}))
        assert [c.eval_id for c in eval_set.eval_cases] == ["case_a", "case_b"]

    @pytest.mark.skipif(os.name == "nt", reason="':' is not a legal filename character on Windows")
    def test_load_existing_path_containing_colon(self, tmp_path):
        """A colon inside an existing full path must not be split as a selector."""
        source = self._write_eval_set(tmp_path, ["case_a", "case_b"])
        colon_path = tmp_path / "drive:like.evalset.json"
        with open(source, "r", encoding="utf-8") as f:
            colon_path.write_text(f.read(), encoding="utf-8")
        eval_set = AgentEvaluator._load_eval_set_from_file(str(colon_path), EvalConfig(criteria={}))
        assert [c.eval_id for c in eval_set.eval_cases] == ["case_a", "case_b"]

    def test_load_with_case_selector(self, tmp_path):
        """"file.json:case_id" selects a single case from the set.

        The full string never exists on disk while the part before the last
        colon does, so the rpartition branch runs on every platform.
        """
        file_path = self._write_eval_set(tmp_path, ["case_a", "case_b"])
        eval_set = AgentEvaluator._load_eval_set_from_file(f"{file_path}:case_b", EvalConfig(criteria={}))
        assert [c.eval_id for c in eval_set.eval_cases] == ["case_b"]
        assert eval_set.eval_set_id == "set1_case_b"

    def test_load_with_unknown_case_selector_raises(self, tmp_path):
        """Selecting a case id that does not exist raises ValueError."""
        file_path = self._write_eval_set(tmp_path, ["case_a"])
        with pytest.raises(ValueError, match="not found"):
            AgentEvaluator._load_eval_set_from_file(f"{file_path}:missing", EvalConfig(criteria={}))

    def test_load_missing_file_raises(self, tmp_path):
        """A non-existing path (with or without ':') raises FileNotFoundError."""
        missing = str(tmp_path / "nope.evalset.json")
        with pytest.raises(FileNotFoundError):
            AgentEvaluator._load_eval_set_from_file(missing, EvalConfig(criteria={}))
