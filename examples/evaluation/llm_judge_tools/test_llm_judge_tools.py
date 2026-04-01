# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Example of registering tools for a judge agent.

Uses llm_rubric_response and defines in the rubric when and how tools must be called.
"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import LLM_EVALUATOR_REGISTRY
from trpc_agent_sdk.tools import FunctionTool


def get_eval_policy() -> str:
    """Tool that the judge must call before scoring.

    Returns the evaluation criteria for this test case. The judge must call this
    tool first, then score strictly against each returned rule.
    """
    return ("Evaluation criteria for this case (3 rules):\n"
            "1. The final answer must include an explicit temperature value (for example, 18 or 18°C).\n"
            "2. The final answer must include a weather condition description (for example, sunny, cloudy, overcast).\n"
            "3. The answer must be directly relevant to the user's question and not off-topic.")


# Register tools for the llm_rubric_response judge agent.
# The rubric already requires calling get_eval_policy first, then scoring by returned rules.
LLM_EVALUATOR_REGISTRY.register_judge_tools(
    "llm_rubric_response",
    [FunctionTool(get_eval_policy)],
)


@pytest.mark.asyncio
async def test_llm_judge_with_tools():
    """Use llm_rubric_response.

    The judge must call get_eval_policy first to get criteria from the rubric,
    then score according to those criteria.
    """
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "judge_tools.evalset.json")

    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="llm_judge_tools_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
