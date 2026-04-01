#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Compose Agent 示例 - 组合编排模式

演示组合模式：并行分析 → 综合报告
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import ChainAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import ParallelAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent():
    """创建组合Agent：并行分析 + 综合报告"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # 质量分析Agent
    quality_analyst = LlmAgent(
        name="quality_analyst",
        model=model,
        description="Analyze content quality",
        instruction="""Analyze content quality: clarity, structure, completeness.
Provide quality score (1-10) and brief feedback.""",
        output_key="quality_analysis",
    )

    # 安全分析Agent
    security_analyst = LlmAgent(
        name="security_analyst",
        model=model,
        description="Analyze security concerns",
        instruction="""Analyze security aspects: data privacy, vulnerabilities.
Provide security score (1-10) and identify risks.""",
        output_key="security_analysis",
    )

    # 创建并行分析阶段
    parallel_analysis_stage = ParallelAgent(
        name="parallel_analysis_team",
        description="Parallel quality and security analysis",
        sub_agents=[quality_analyst, security_analyst],
    )

    # 报告生成Agent
    report_generator = LlmAgent(
        name="report_generator",
        model=model,
        description="Generate comprehensive report",
        instruction="""Generate analysis report based on:

Quality Analysis: {quality_analysis}
Security Analysis: {security_analysis}

Create summary with overall assessment and recommendations.""",
        output_key="final_report",
    )

    # 组合：并行分析 → 综合报告
    return ChainAgent(
        name="analysis_pipeline",
        description="Parallel analysis → integrated report",
        sub_agents=[parallel_analysis_stage, report_generator],
    )


async def run_agent():
    """运行组合Agent演示"""

    # 定义应用和用户信息
    APP_NAME = "compose_demo"
    USER_ID = "demo_user"

    print("=" * 40)
    print("Compose Agent 演示 - 组合编排")
    print("=" * 40)

    # 创建组合Agent
    compose_agent = create_agent()

    # 创建Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=compose_agent, session_service=session_service)

    # 简化的测试内容
    test_content = """
    Smart Home Security System

    Our AI-powered security system provides 24/7 monitoring with facial recognition,
    motion detection, and mobile alerts. The system stores user data including video
    recordings and personal information for security analysis.

    Features:
    - Real-time monitoring
    - Mobile app notifications
    - Cloud storage for recordings
    - User data analytics
    """

    print("分析内容：")
    print(test_content.strip())
    print("\n执行过程：")

    user_message = Content(parts=[Part.from_text(text=test_content)])

    async for event in runner.run_async(user_id=USER_ID, session_id=str(uuid.uuid4()), new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.text:
                        if event.author == "report_generator":
                            print("\n[综合报告]")
                            print(part.text)
                        else:
                            print(f"[{event.author}] {part.text}")
                            print("-" * 30)


if __name__ == "__main__":
    print("🔧 Compose Agent 示例")
    print("展示组合编排：并行分析 → 综合报告")
    asyncio.run(run_agent())
