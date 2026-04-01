# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""回调示例：在评测中注册 8 个生命周期 Callbacks，打日志并演示 context 传递。"""

import os

import pytest
from trpc_agent_sdk.evaluation import AfterEvaluateCaseArgs
from trpc_agent_sdk.evaluation import AfterEvaluateSetArgs
from trpc_agent_sdk.evaluation import AfterInferenceCaseArgs
from trpc_agent_sdk.evaluation import AfterInferenceSetArgs
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import BeforeEvaluateCaseArgs
from trpc_agent_sdk.evaluation import BeforeEvaluateSetArgs
from trpc_agent_sdk.evaluation import BeforeInferenceCaseArgs
from trpc_agent_sdk.evaluation import BeforeInferenceSetArgs
from trpc_agent_sdk.evaluation import Callback
from trpc_agent_sdk.evaluation import CallbackResult
from trpc_agent_sdk.evaluation import Callbacks

triggered: list[str] = []


def before_inference_set(ctx, args: BeforeInferenceSetArgs):
    triggered.append("before_inference_set")
    print("[callback] 推理集开始", args.request.eval_set_id, flush=True)
    return None


def after_inference_set(ctx, args: AfterInferenceSetArgs):
    triggered.append("after_inference_set")
    n = len(args.results) if args.results else 0
    print("[callback] 推理集结束，共", n, "个用例", flush=True)
    return None


def before_inference_case(ctx, args: BeforeInferenceCaseArgs):
    triggered.append("before_inference_case")
    print("[callback] 用例推理开始", args.eval_case_id, flush=True)
    return None


def after_inference_case(ctx, args: AfterInferenceCaseArgs):
    triggered.append("after_inference_case")
    print("[callback] 用例推理结束", args.result.eval_case_id, flush=True)
    return None


def before_evaluate_set(ctx, args: BeforeEvaluateSetArgs):
    triggered.append("before_evaluate_set")
    n = len(args.request.inference_results)
    print("[callback] 打分集开始 cases=", n, flush=True)
    return CallbackResult(context={"phase": "evaluate"})


def after_evaluate_set(ctx, args: AfterEvaluateSetArgs):
    triggered.append("after_evaluate_set")
    n = len(args.result.eval_case_results) if args.result else 0
    phase = (ctx.get("context") or {}).get("phase", "?")
    print("[callback] 打分集结束，共", n, "个用例，ctx.phase=", phase, flush=True)
    return None


def before_evaluate_case(ctx, args: BeforeEvaluateCaseArgs):
    triggered.append("before_evaluate_case")
    print("[callback] 用例打分开始", args.eval_case_id, flush=True)
    return None


def after_evaluate_case(ctx, args: AfterEvaluateCaseArgs):
    triggered.append("after_evaluate_case")
    print("[callback] 用例打分结束", args.result.eval_id, flush=True)
    return None


@pytest.mark.asyncio
async def test_with_callbacks():
    triggered.clear()
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "callbacks_example.evalset.json")

    callbacks = Callbacks()
    callbacks.register(
        "demo",
        Callback(
            before_inference_set=before_inference_set,
            after_inference_set=after_inference_set,
            before_inference_case=before_inference_case,
            after_inference_case=after_inference_case,
            before_evaluate_set=before_evaluate_set,
            after_evaluate_set=after_evaluate_set,
            before_evaluate_case=before_evaluate_case,
            after_evaluate_case=after_evaluate_case,
        ),
    )

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="weather_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        callbacks=callbacks,
    )

    expected = [
        "before_inference_set",
        "before_inference_case",
        "after_inference_case",
        "after_inference_set",
        "before_evaluate_set",
        "before_evaluate_case",
        "after_evaluate_case",
        "after_evaluate_set",
    ]
    assert triggered == expected, triggered
