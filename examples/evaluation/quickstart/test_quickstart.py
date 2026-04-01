# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Quickstart 天气 Agent 评测测试"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_quickstart_with_eval_set():
    """使用单个 evalset 测试 quickstart 天气 Agent"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "weather_agent.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="weather_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
