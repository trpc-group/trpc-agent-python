#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Parallel Agent 示例 - 并行内容审查

演示并行执行：质量审查 + 安全审查
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import ParallelAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent():
    """创建并行审查Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # 质量审查Agent
    quality_reviewer = LlmAgent(
        name="quality_reviewer",
        model=model,
        description="Review content quality",
        instruction="""Review content quality: clarity, accuracy, readability.
Provide quality score (1-10) and brief feedback.""",
        output_key="quality_review",
    )

    # 安全审查Agent
    security_reviewer = LlmAgent(
        name="security_reviewer",
        model=model,
        description="Review content security",
        instruction="""Review security concerns: data privacy, vulnerabilities.
Provide security score (1-10) and identify risks.""",
        output_key="security_review",
    )

    # 创建并行Agent
    return ParallelAgent(
        name="review_panel",
        description="Parallel review: quality + security",
        sub_agents=[quality_reviewer, security_reviewer],
    )


async def run_agent():
    """运行并行Agent演示"""

    # 定义应用和用户信息
    APP_NAME = "parallel_demo"
    USER_ID = "demo_user"

    print("=" * 40)
    print("Parallel Agent 演示 - 并行审查")
    print("=" * 40)

    # 创建并行Agent
    parallel_agent = create_agent()

    # 创建Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=parallel_agent, session_service=session_service)

    # 简化测试内容
    test_content = """
    AI Smart Home System - Our system collects user data including personal
    preferences and usage patterns. Data is stored in cloud servers with
    basic encryption. Users can access the system through mobile apps.
    """

    print("审查内容:")
    print(test_content.strip())
    print("\n并行审查中:")

    user_message = Content(parts=[Part.from_text(text=test_content)])

    # 生成session_id用于后续获取结果
    session_id = str(uuid.uuid4())

    async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(f"[{event.author}] 完成")

    # 获取并行审查结果
    session = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    print("\n审查结果:")
    print("-" * 30)

    # 显示各Agent的审查结果
    if session and session.state:
        if "quality_review" in session.state:
            print("\n[质量审查]")
            print(session.state["quality_review"])

        if "security_review" in session.state:
            print("\n[安全审查]")
            print(session.state["security_review"])


if __name__ == "__main__":
    print("🔀 Parallel Agent 示例")
    print("展示并行处理：质量+安全审查")
    asyncio.run(run_agent())
