# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""自定义 Runner 示例：使用自建 Runner（agent + session_service）跑评测。"""

import os

import pytest
from agent import root_agent
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService


@pytest.mark.asyncio
async def test_evaluate_with_custom_runner():
    """使用自定义 Runner 执行评测：自建 Runner 负责推理，打分由框架完成。"""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    eval_set_path = os.path.join(test_dir, "agent", "custom_runner_example.evalset.json")

    # 自建会话服务（可替换为 Redis/SQL 等）
    session_service = InMemorySessionService()

    # 构造 Runner：与线上/本地部署使用同一 Runner 形态，便于复用环境
    runner = Runner(
        app_name="weather_agent",
        agent=root_agent,
        session_service=session_service,
    )

    # 传入 runner 后，推理由该 Runner 执行，打分仍由评测框架完成
    await AgentEvaluator.evaluate(
        agent_module="agent",
        agent_name="weather_agent",
        eval_dataset_file_path_or_dir=eval_set_path,
        runner=runner,
    )
