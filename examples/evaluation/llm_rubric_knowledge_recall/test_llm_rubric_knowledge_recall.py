# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""llm_rubric_knowledge_recall 评估器示例测试"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_llm_rubric_knowledge_recall_demo():
    """使用 llm_rubric_knowledge_recall 指标：裁判根据轨迹中的知识检索结果与 rubrics 判定召回质量。"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "llm_rubric_knowledge_recall.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="llm_rubric_knowledge_recall_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
