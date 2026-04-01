# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""llm_final_response 评估器示例测试"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_llm_final_response_demo():
    """使用 llm_final_response 指标评测 Agent：裁判模型对比实际回答与参考答案。"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "llm_final_response.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="llm_final_response_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
