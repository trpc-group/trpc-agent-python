# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation lifecycle callbacks. Register hooks at inference/evaluate set/case boundaries."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from ._eval_result import EvalCaseResult
from ._eval_service_base import EvaluateRequest
from ._eval_service_base import InferenceRequest
from ._eval_service_base import InferenceResult


class CallbackPoint(Enum):
    """Lifecycle point for evaluation callbacks. Value is the Callback attribute name (snake_case)."""

    BEFORE_INFERENCE_SET = "before_inference_set"
    AFTER_INFERENCE_SET = "after_inference_set"
    BEFORE_INFERENCE_CASE = "before_inference_case"
    AFTER_INFERENCE_CASE = "after_inference_case"
    BEFORE_EVALUATE_SET = "before_evaluate_set"
    AFTER_EVALUATE_SET = "after_evaluate_set"
    BEFORE_EVALUATE_CASE = "before_evaluate_case"
    AFTER_EVALUATE_CASE = "after_evaluate_case"


@dataclass
class EvalSetRunResult:
    """Result of a single eval set run. Aligns with Go EvalSetRunResult."""

    app_name: str = ""
    eval_set_id: str = ""
    eval_case_results: list[EvalCaseResult] = field(default_factory=list)


@dataclass
class CallbackResult:
    """Result of a lifecycle callback. Context is passed to subsequent operations."""

    context: Any = None


@dataclass
class BeforeInferenceSetArgs:
    """Arguments for before inference set. Request can be modified by callbacks."""

    request: InferenceRequest


@dataclass
class AfterInferenceSetArgs:
    """Arguments for after inference set."""

    request: InferenceRequest
    results: list[InferenceResult]
    error: Optional[Exception]
    start_time: float


@dataclass
class BeforeInferenceCaseArgs:
    """Arguments for before inference case."""

    request: InferenceRequest
    eval_case_id: str
    session_id: str


@dataclass
class AfterInferenceCaseArgs:
    """Arguments for after inference case."""

    request: InferenceRequest
    result: InferenceResult
    error: Optional[Exception]
    start_time: float


@dataclass
class BeforeEvaluateSetArgs:
    """Arguments for before evaluate set. Request can be modified."""

    request: EvaluateRequest


@dataclass
class AfterEvaluateSetArgs:
    """Arguments for after evaluate set."""

    request: EvaluateRequest
    result: Optional[EvalSetRunResult]
    error: Optional[Exception]
    start_time: float


@dataclass
class BeforeEvaluateCaseArgs:
    """Arguments for before evaluate case."""

    request: EvaluateRequest
    eval_case_id: str


@dataclass
class AfterEvaluateCaseArgs:
    """Arguments for after evaluate case."""

    request: EvaluateRequest
    inference_result: InferenceResult
    result: EvalCaseResult
    error: Optional[Exception]
    start_time: float


# (ctx, args) -> Optional[CallbackResult] | Any
CallbackFn = Callable[
    [dict[str, Any], Any],
    Union[Optional[CallbackResult], Any],
]


class Callback:
    """Group of optional lifecycle callbacks. Register any subset of the 8 hooks.

    Construct with keyword args only: Callback(before_evaluate_set=fn, ...).
    """

    __slots__ = ("_hooks", )

    def __init__(self, **kwargs: Optional[CallbackFn]) -> None:
        self._hooks = {p: kwargs.get(p.value) for p in CallbackPoint}

    def get(self, point: CallbackPoint) -> Optional[CallbackFn]:
        """Return the hook for the given lifecycle point, or None if not set."""
        return self._hooks.get(point)


class Callbacks:
    """Register and run evaluation lifecycle callbacks at inference/evaluate set/case boundaries.

    Example:
        from trpc_agent_sdk.evaluation import Callbacks, Callback, CallbackResult, BeforeEvaluateSetArgs

        def before_eval_set(ctx, args: BeforeEvaluateSetArgs):
            print("Start evaluating", len(args.request.inference_results), "cases")
            return CallbackResult(context={"run_id": "xxx"})

        callbacks = Callbacks()
        callbacks.register("my_plugin", Callback(before_evaluate_set=before_eval_set))
        await AgentEvaluator.evaluate_eval_set(..., callbacks=callbacks)
    """

    def __init__(self) -> None:
        self._hooks: dict[CallbackPoint, list[tuple[str, CallbackFn]]] = {p: [] for p in CallbackPoint}

    def register(self, name: str, callback: Optional[Callback]) -> "Callbacks":
        """Register a named callback component. Name is used in error messages. Any subset of hooks can be set."""
        if not callback:
            return self
        for point in CallbackPoint:
            fn = callback.get(point)
            if fn is not None:
                self._hooks[point].append((name, fn))
        return self

    def _register_point(self, point: CallbackPoint, name: str, fn: CallbackFn) -> "Callbacks":
        return self.register(name, Callback(**{point.value: fn}))

    def register_before_inference_set(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.BEFORE_INFERENCE_SET, name, fn)

    def register_after_inference_set(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.AFTER_INFERENCE_SET, name, fn)

    def register_before_inference_case(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.BEFORE_INFERENCE_CASE, name, fn)

    def register_after_inference_case(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.AFTER_INFERENCE_CASE, name, fn)

    def register_before_evaluate_set(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.BEFORE_EVALUATE_SET, name, fn)

    def register_after_evaluate_set(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.AFTER_EVALUATE_SET, name, fn)

    def register_before_evaluate_case(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.BEFORE_EVALUATE_CASE, name, fn)

    def register_after_evaluate_case(self, name: str, fn: CallbackFn) -> "Callbacks":
        return self._register_point(CallbackPoint.AFTER_EVALUATE_CASE, name, fn)

    def get_hooks(self, point: CallbackPoint) -> list[tuple[str, CallbackFn]]:
        return self._hooks[point]


class CallbacksRunner:
    """Runner for executing registered callbacks. Used by the framework only."""

    def __init__(self, callbacks: Callbacks) -> None:
        self._callbacks = callbacks

    def _wrap_error(self, point: str, idx: int, name: str, err: Exception) -> Exception:
        out = RuntimeError(f"{point} callback[{idx}] ({name}): {err}")
        out.__cause__ = err
        return out

    def _get_context_from_result(self, result: Any) -> Any:
        if result is None:
            return None
        if isinstance(result, CallbackResult):
            return result.context
        return None

    async def _call_with_recovery(
        self,
        ctx: dict[str, Any],
        point: str,
        idx: int,
        name: str,
        fn: Callable[..., Any],
        args: Any,
    ) -> tuple[Any, Optional[Exception]]:
        try:
            if inspect.iscoroutinefunction(fn):
                out = await fn(ctx, args)
            else:
                out = fn(ctx, args)
            return (out, None)
        except Exception as e:
            return (None, self._wrap_error(point, idx, name, e))

    async def _run_hooks(
        self,
        hooks: list[tuple[str, Callable[..., Any]]],
        point: str,
        args: Any,
        ctx: Optional[dict[str, Any]] = None,
    ) -> tuple[Any, Optional[Exception]]:
        """Execute a list of hooks in order; merge context from results; return (last_result, err)."""
        if not hooks:
            return (None, None)
        run_ctx = ctx if ctx is not None else {}
        last_result: Any = None
        for idx, (name, fn) in enumerate(hooks):
            result, err = await self._call_with_recovery(run_ctx, point, idx, name, fn, args)
            if err is not None:
                e = RuntimeError(f"execute {point} callbacks: {err}")
                e.__cause__ = err
                return (None, e)
            if result is not None:
                last_result = result
            c = self._get_context_from_result(result)
            if c is not None:
                run_ctx["context"] = c
        return (last_result, None)

    async def _run_impl(
        self,
        point: CallbackPoint,
        args: Any,
        ctx: Optional[dict[str, Any]] = None,
    ) -> tuple[Any, Optional[Exception]]:
        hooks = self._callbacks.get_hooks(point)
        return await self._run_hooks(hooks, point.value, args, ctx)

    async def run_before_inference_set(self, request: InferenceRequest, run_ctx: dict[str, Any]) -> None:
        _, err = await self._run_impl(
            CallbackPoint.BEFORE_INFERENCE_SET,
            BeforeInferenceSetArgs(request=request),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_after_inference_set(
        self,
        request: InferenceRequest,
        results: list[InferenceResult],
        error: Optional[Exception],
        start_time: float,
        run_ctx: dict[str, Any],
    ) -> None:
        _, err = await self._run_impl(
            CallbackPoint.AFTER_INFERENCE_SET,
            AfterInferenceSetArgs(
                request=request,
                results=results,
                error=error,
                start_time=start_time,
            ),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_before_inference_case(
        self,
        request: InferenceRequest,
        eval_case_id: str,
        session_id: str,
        run_ctx: dict[str, Any],
    ) -> None:
        _, err = await self._run_impl(
            CallbackPoint.BEFORE_INFERENCE_CASE,
            BeforeInferenceCaseArgs(
                request=request,
                eval_case_id=eval_case_id,
                session_id=session_id,
            ),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_after_inference_case(
        self,
        request: InferenceRequest,
        result: InferenceResult,
        error: Optional[Exception],
        start_time: float,
        run_ctx: dict[str, Any],
    ) -> None:
        _, err = await self._run_impl(
            CallbackPoint.AFTER_INFERENCE_CASE,
            AfterInferenceCaseArgs(
                request=request,
                result=result,
                error=error,
                start_time=start_time,
            ),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_before_evaluate_set(self, request: EvaluateRequest, run_ctx: dict[str, Any]) -> None:
        _, err = await self._run_impl(
            CallbackPoint.BEFORE_EVALUATE_SET,
            BeforeEvaluateSetArgs(request=request),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_after_evaluate_set(
        self,
        request: EvaluateRequest,
        result: EvalSetRunResult,
        error: Optional[Exception],
        start_time: float,
        run_ctx: dict[str, Any],
    ) -> None:
        _, err = await self._run_impl(
            CallbackPoint.AFTER_EVALUATE_SET,
            AfterEvaluateSetArgs(
                request=request,
                result=result,
                error=error,
                start_time=start_time,
            ),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_before_evaluate_case(
        self,
        request: EvaluateRequest,
        eval_case_id: str,
        run_ctx: dict[str, Any],
    ) -> None:
        _, err = await self._run_impl(
            CallbackPoint.BEFORE_EVALUATE_CASE,
            BeforeEvaluateCaseArgs(
                request=request,
                eval_case_id=eval_case_id,
            ),
            run_ctx,
        )
        if err is not None:
            raise err

    async def run_after_evaluate_case(
        self,
        request: EvaluateRequest,
        inference_result: InferenceResult,
        result: EvalCaseResult,
        error: Optional[Exception],
        start_time: float,
        run_ctx: dict[str, Any],
    ) -> None:
        _, err = await self._run_impl(
            CallbackPoint.AFTER_EVALUATE_CASE,
            AfterEvaluateCaseArgs(
                request=request,
                inference_result=inference_result,
                result=result,
                error=error,
                start_time=start_time,
            ),
            run_ctx,
        )
        if err is not None:
            raise err
