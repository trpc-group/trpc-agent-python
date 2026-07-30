# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""书籍查找 Agent 测试 - 仿照 ADK AgentEvaluator"""

import os
import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_webui_with_eval_set():
    """使用评估集测试 WebUI 书籍查找 Agent"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "agent.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
