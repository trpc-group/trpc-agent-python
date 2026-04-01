# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""为裁判 Agent 注册工具的示例：使用 llm_rubric_response，在 rubric 中规定工具的调用时机与用法。"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import LLM_EVALUATOR_REGISTRY
from trpc_agent_sdk.tools import FunctionTool


def get_eval_policy() -> str:
    """裁判在打分前必须调用的工具：返回本用例的判定标准。裁判须先调用本工具获取标准，再仅按返回的条款逐条判定。"""
    return ("本用例判定标准（共 3 条）：\n"
            "1. 最终回答须包含明确的温度数值（如 18、18°C）。\n"
            "2. 最终回答须包含天气状况描述（如晴、多云、阴）。\n"
            "3. 回答须与用户问题直接相关，不得答非所问。")


# 为 llm_rubric_response 的 judge agent 注册工具；rubric 中已规定「必须先调用 get_eval_policy 再按返回条款判定」
LLM_EVALUATOR_REGISTRY.register_judge_tools(
    "llm_rubric_response",
    [FunctionTool(get_eval_policy)],
)


@pytest.mark.asyncio
async def test_llm_judge_with_tools():
    """使用 llm_rubric_response：裁判按 rubric 须先调用 get_eval_policy 获取判定标准，再按标准条款打分。"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "judge_tools.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="llm_judge_tools_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
