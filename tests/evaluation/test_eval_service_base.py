# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for eval service base (_eval_service_base)."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvaluateConfig
from trpc_agent_sdk.evaluation import EvaluateRequest
from trpc_agent_sdk.evaluation import InferenceConfig
from trpc_agent_sdk.evaluation import InferenceRequest
from trpc_agent_sdk.evaluation import InferenceResult
from trpc_agent_sdk.evaluation import InferenceStatus


class TestInferenceStatus:
    """Test suite for InferenceStatus enum."""

    def test_values(self):
        """Test InferenceStatus has expected values."""
        assert hasattr(InferenceStatus, "SUCCESS")
        assert hasattr(InferenceStatus, "FAILURE")


class TestInferenceRequest:
    """Test suite for InferenceRequest."""

    def test_inference_request_minimal(self):
        """Test InferenceRequest with required fields."""
        req = InferenceRequest(
            app_name="app1",
            eval_set_id="set1",
            inference_config=InferenceConfig(),
        )
        assert req.app_name == "app1"
        assert req.eval_set_id == "set1"
        assert req.eval_case_ids is None


class TestInferenceResult:
    """Test suite for InferenceResult."""

    def test_inference_result(self):
        """Test InferenceResult creation."""
        res = InferenceResult(
            app_name="app1",
            eval_set_id="set1",
            eval_case_id="case_001",
            status=InferenceStatus.SUCCESS,
            inferences=[],
        )
        assert res.status == InferenceStatus.SUCCESS
        assert res.eval_case_id == "case_001"
        assert res.inferences == []


class TestEvaluateConfig:
    """Test suite for EvaluateConfig (eval_service_base)."""

    def test_evaluate_config(self):
        """Test EvaluateConfig model."""
        from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric
        cfg = EvaluateConfig(eval_metrics=[])
        assert cfg.eval_metrics == []
        assert cfg.parallelism == 4


class TestEvaluateRequest:
    """Test suite for EvaluateRequest."""

    def test_evaluate_request(self):
        """Test EvaluateRequest creation."""
        from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric
        cfg = EvaluateConfig(eval_metrics=[])
        req = EvaluateRequest(
            inference_results=[],
            evaluate_config=cfg,
        )
        assert req.evaluate_config is not None
        assert req.inference_results == []
