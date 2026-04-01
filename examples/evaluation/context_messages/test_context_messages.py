# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""上下文注入示例：在用例中配置 context_messages，每轮推理前注入到会话。"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_context_messages():
    """用例带 context_messages，推理前会注入到会话上下文。"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "context_example.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="weather_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
