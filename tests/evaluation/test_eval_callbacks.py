# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation callbacks (_eval_callbacks)."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import AfterEvaluateSetArgs
from trpc_agent_sdk.evaluation import BeforeInferenceSetArgs
from trpc_agent_sdk.evaluation import CallbackPoint
from trpc_agent_sdk.evaluation import CallbackResult
from trpc_agent_sdk.evaluation import EvalSetRunResult


class TestCallbackPoint:
    """Test suite for CallbackPoint enum."""

    def test_values(self):
        """Test CallbackPoint has all lifecycle points."""
        assert CallbackPoint.BEFORE_INFERENCE_SET.value == "before_inference_set"
        assert CallbackPoint.AFTER_EVALUATE_CASE.value == "after_evaluate_case"
        assert len(CallbackPoint) == 8


class TestEvalSetRunResult:
    """Test suite for EvalSetRunResult."""

    def test_defaults(self):
        """Test EvalSetRunResult default fields."""
        r = EvalSetRunResult()
        assert r.app_name == ""
        assert r.eval_set_id == ""
        assert r.eval_case_results == []


class TestCallbackResult:
    """Test suite for CallbackResult."""

    def test_callback_result_context(self):
        """Test CallbackResult with context."""
        r = CallbackResult(context={"key": "value"})
        assert r.context == {"key": "value"}


class TestBeforeInferenceSetArgs:
    """Test suite for BeforeInferenceSetArgs."""

    def test_before_inference_set_args(self):
        """Test BeforeInferenceSetArgs requires request."""
        from trpc_agent_sdk.evaluation._eval_service_base import InferenceRequest, InferenceConfig
        req = InferenceRequest(
            app_name="a",
            eval_set_id="s",
            inference_config=InferenceConfig(),
        )
        args = BeforeInferenceSetArgs(request=req)
        assert args.request == req


class TestAfterEvaluateSetArgs:
    """Test suite for AfterEvaluateSetArgs."""

    def test_after_evaluate_set_args(self):
        """Test AfterEvaluateSetArgs fields."""
        from trpc_agent_sdk.evaluation._eval_service_base import (
            EvaluateRequest,
            EvaluateConfig,
        )
        from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric
        cfg = EvaluateConfig(eval_metrics=[])
        req = EvaluateRequest(
            inference_results=[],
            evaluate_config=cfg,
        )
        args = AfterEvaluateSetArgs(
            request=req,
            result=None,
            error=None,
            start_time=0.0,
        )
        assert args.request == req
        assert args.result is None
        assert args.error is None
        assert args.start_time == 0.0
