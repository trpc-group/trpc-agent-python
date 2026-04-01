# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Trace 模式示例：使用预录制的对话轨迹参与打分，不调用 Agent 推理。"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_trace_mode():
    """Trace 模式：跳过推理，用 evalset 中的 actual_conversation 作为实际轨迹参与评估。"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "trace_example.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="weather_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
