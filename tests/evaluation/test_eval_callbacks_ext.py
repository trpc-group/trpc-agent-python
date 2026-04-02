# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Extended tests for evaluation callbacks: Callback, Callbacks, CallbacksRunner."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import Callback
from trpc_agent_sdk.evaluation import CallbackPoint
from trpc_agent_sdk.evaluation import CallbackResult
from trpc_agent_sdk.evaluation import Callbacks
from trpc_agent_sdk.evaluation import CallbacksRunner
from trpc_agent_sdk.evaluation._eval_service_base import (
    EvaluateConfig,
    EvaluateRequest,
    InferenceConfig,
    InferenceRequest,
    InferenceResult,
)
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus


def _make_inference_request():
    return InferenceRequest(app_name="a", eval_set_id="s", inference_config=InferenceConfig())


def _make_evaluate_request():
    return EvaluateRequest(inference_results=[], evaluate_config=EvaluateConfig(eval_metrics=[]))


class TestCallback:
    """Test suite for Callback."""

    def test_get_registered_hook(self):
        """Test get returns registered hook."""
        fn = lambda ctx, args: None
        cb = Callback(before_inference_set=fn)
        assert cb.get(CallbackPoint.BEFORE_INFERENCE_SET) is fn

    def test_get_unregistered_returns_none(self):
        """Test get returns None for unregistered hook."""
        cb = Callback()
        assert cb.get(CallbackPoint.BEFORE_INFERENCE_SET) is None


class TestCallbacks:
    """Test suite for Callbacks."""

    def test_register_none_callback(self):
        """Test registering None callback is no-op."""
        cbs = Callbacks()
        cbs.register("test", None)
        assert cbs.get_hooks(CallbackPoint.BEFORE_INFERENCE_SET) == []

    def test_register_adds_hooks(self):
        """Test register adds hooks correctly."""
        fn = lambda ctx, args: None
        cbs = Callbacks()
        cbs.register("test", Callback(before_inference_set=fn))
        hooks = cbs.get_hooks(CallbackPoint.BEFORE_INFERENCE_SET)
        assert len(hooks) == 1
        assert hooks[0] == ("test", fn)

    def test_register_returns_self(self):
        """Test register returns self for chaining."""
        cbs = Callbacks()
        result = cbs.register("test", Callback())
        assert result is cbs

    def test_register_helpers(self):
        """Test register_before/after helpers."""
        fn = lambda ctx, args: None
        cbs = Callbacks()
        cbs.register_before_inference_set("t", fn)
        cbs.register_after_inference_set("t", fn)
        cbs.register_before_inference_case("t", fn)
        cbs.register_after_inference_case("t", fn)
        cbs.register_before_evaluate_set("t", fn)
        cbs.register_after_evaluate_set("t", fn)
        cbs.register_before_evaluate_case("t", fn)
        cbs.register_after_evaluate_case("t", fn)
        for point in CallbackPoint:
            assert len(cbs.get_hooks(point)) == 1


class TestCallbacksRunner:
    """Test suite for CallbacksRunner."""

    async def test_sync_callback_executed(self):
        """Test sync callback is executed."""
        called = []
        cbs = Callbacks()
        cbs.register_before_inference_set("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        await runner.run_before_inference_set(_make_inference_request(), {})
        assert len(called) == 1

    async def test_async_callback_executed(self):
        """Test async callback is executed."""
        called = []
        async def hook(ctx, args):
            called.append(True)

        cbs = Callbacks()
        cbs.register_before_inference_set("t", hook)
        runner = CallbacksRunner(cbs)
        await runner.run_before_inference_set(_make_inference_request(), {})
        assert len(called) == 1

    async def test_callback_error_raises(self):
        """Test callback error is raised as RuntimeError."""
        def bad_hook(ctx, args):
            raise ValueError("boom")

        cbs = Callbacks()
        cbs.register_before_inference_set("t", bad_hook)
        runner = CallbacksRunner(cbs)
        with pytest.raises(RuntimeError):
            await runner.run_before_inference_set(_make_inference_request(), {})

    async def test_callback_context_propagation(self):
        """Test CallbackResult context is propagated."""
        def hook1(ctx, args):
            return CallbackResult(context={"key": "val"})

        ctx_values = []
        def hook2(ctx, args):
            ctx_values.append(ctx.get("context"))

        cbs = Callbacks()
        cbs.register_before_inference_set("h1", hook1)
        cbs.register_before_inference_set("h2", hook2)
        runner = CallbacksRunner(cbs)
        await runner.run_before_inference_set(_make_inference_request(), {})
        assert ctx_values == [{"key": "val"}]

    async def test_no_hooks_no_error(self):
        """Test no hooks registered runs without error."""
        cbs = Callbacks()
        runner = CallbacksRunner(cbs)
        await runner.run_before_inference_set(_make_inference_request(), {})

    async def test_run_after_inference_set(self):
        """Test run_after_inference_set works."""
        called = []
        cbs = Callbacks()
        cbs.register_after_inference_set("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        await runner.run_after_inference_set(_make_inference_request(), [], None, 0.0, {})
        assert len(called) == 1

    async def test_run_before_inference_case(self):
        """Test run_before_inference_case works."""
        called = []
        cbs = Callbacks()
        cbs.register_before_inference_case("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        await runner.run_before_inference_case(_make_inference_request(), "c1", "s1", {})
        assert len(called) == 1

    async def test_run_after_inference_case(self):
        """Test run_after_inference_case works."""
        called = []
        cbs = Callbacks()
        cbs.register_after_inference_case("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        ir = InferenceResult(app_name="a", eval_set_id="s", eval_case_id="c1", session_id="s1")
        await runner.run_after_inference_case(_make_inference_request(), ir, None, 0.0, {})
        assert len(called) == 1

    async def test_run_before_evaluate_set(self):
        """Test run_before_evaluate_set works."""
        called = []
        cbs = Callbacks()
        cbs.register_before_evaluate_set("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        await runner.run_before_evaluate_set(_make_evaluate_request(), {})
        assert len(called) == 1

    async def test_run_after_evaluate_set(self):
        """Test run_after_evaluate_set works."""
        called = []
        cbs = Callbacks()
        cbs.register_after_evaluate_set("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        await runner.run_after_evaluate_set(_make_evaluate_request(), None, None, 0.0, {})
        assert len(called) == 1

    async def test_run_before_evaluate_case(self):
        """Test run_before_evaluate_case works."""
        called = []
        cbs = Callbacks()
        cbs.register_before_evaluate_case("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        await runner.run_before_evaluate_case(_make_evaluate_request(), "c1", {})
        assert len(called) == 1

    async def test_run_after_evaluate_case(self):
        """Test run_after_evaluate_case works."""
        called = []
        cbs = Callbacks()
        cbs.register_after_evaluate_case("t", lambda ctx, args: called.append(True))
        runner = CallbacksRunner(cbs)
        ir = InferenceResult(app_name="a", eval_set_id="s", eval_case_id="c1", session_id="s1")
        cr = EvalCaseResult(eval_id="c1", final_eval_status=EvalStatus.PASSED,
                            overall_eval_metric_results=[], eval_metric_result_per_invocation=[], session_id="s1")
        await runner.run_after_evaluate_case(_make_evaluate_request(), ir, cr, None, 0.0, {})
        assert len(called) == 1

    async def test_wrap_error_format(self):
        """Test _wrap_error produces correct message."""
        cbs = Callbacks()
        runner = CallbacksRunner(cbs)
        err = runner._wrap_error("before_eval_set", 0, "my_hook", ValueError("oops"))
        assert "my_hook" in str(err)
        assert "oops" in str(err)
        assert err.__cause__ is not None

    async def test_get_context_from_result_none(self):
        """Test _get_context_from_result with None returns None."""
        cbs = Callbacks()
        runner = CallbacksRunner(cbs)
        assert runner._get_context_from_result(None) is None
        assert runner._get_context_from_result("not a callback result") is None
