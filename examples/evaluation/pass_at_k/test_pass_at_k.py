# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""pass@k / pass^k 示例：多轮运行后计算 pass@1、pass@5、pass^2。"""

import os

import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator


@pytest.mark.asyncio
async def test_pass_at_k():
    """After multiple rounds of execution, parse (n, c), calculate pass@k and pass^k."""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "weather_agent.evalset.json")

    # test_config.json is configured with num_runs: 5, will run 5 rounds
    executer = AgentEvaluator.get_executer(
        agent_module="agent",
        agent_name="weather_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        print_detailed_results=True,
    )
    try:
        await executer.evaluate()
    finally:
        result = executer.get_result()
        if result is not None:
            nc_by_set = AgentEvaluator.parse_pass_nc(result)
            for eval_set_id, nc in nc_by_set.items():
                n, c = nc.n, nc.c
                pass_1 = AgentEvaluator.pass_at_k(n, c, 1)
                pass_5 = AgentEvaluator.pass_at_k(n, c, 5)
                pass_hat_2 = AgentEvaluator.pass_hat_k(n, c, 2)
                print(f"EvalSet {eval_set_id}: n={n}, c={c}, "
                      f"pass@1={pass_1:.4f}, pass@5={pass_5:.4f}, pass^2={pass_hat_2:.4f}")
