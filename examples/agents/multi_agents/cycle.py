#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Cycle Agent 示例 - 循环执行的内容改进

演示如何使用 CycleAgent 通过 output_key 和 exit 工具实现迭代改进：
内容创作 → 质量评估 → 内容改进 → 再评估... 直到满足要求
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import CycleAgent
from trpc_agent_sdk.agents import InvocationContext
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def exit_refinement_loop(tool_context: InvocationContext):
    """停止内容改进循环的工具函数

    这是Cycle Agent的核心退出机制：
    - 通过设置 tool_context.actions.escalate = True 来主动退出循环
    - 这是确定性的退出方式，不依赖LLM的判断
    - 与max_iterations一起提供双重保护，防止无限循环
    """
    print("  ✅ Content evaluator: Content meets quality standards, exiting loop")
    tool_context.actions.escalate = True  # 关键：设置escalate标志退出循环
    return {"status": "content_approved", "message": "Content quality is satisfactory"}


def create_agent():
    """创建内容改进的循环Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # 内容创作Agent
    content_writer = LlmAgent(
        name="content_writer",
        model=model,
        description="Create and refine content based on requirements",
        instruction="""Create high-quality content based on the user's request.

If this is the first iteration, create original content.
If there's existing content with feedback, improve it based on the suggestions:

Existing content: {current_content}
Feedback: {feedback}

Focus on:
- Clear and engaging writing
- Proper structure and flow
- Accuracy and completeness
- Professional tone

Output only the improved content.""",
        output_key="current_content",  # 将当前内容保存到状态变量
    )

    # 内容评估Agent
    content_evaluator = LlmAgent(
        name="content_evaluator",
        model=model,
        description="Evaluate content quality and decide if refinement is needed",
        instruction="""Evaluate the following content for quality:

{current_content}

Assessment criteria:
- Clarity and readability (score 1-10)
- Structure and organization (score 1-10)
- Completeness and accuracy (score 1-10)
- Professional tone (score 1-10)

If ALL scores are 8 or above, call the exit_refinement_loop tool immediately.
If any score is below 8, provide specific feedback for improvement but do NOT call the tool.

Format your response as:
Clarity: X/10
Structure: X/10
Completeness: X/10
Tone: X/10

Feedback: [specific suggestions for improvement if needed]""",
        output_key="feedback",  # 将反馈保存到状态变量
        tools=[FunctionTool(exit_refinement_loop)],
    )

    # 创建循环Agent
    return CycleAgent(
        name="content_refinement_loop",
        description="Iterative content refinement: write → evaluate → improve",
        max_iterations=5,  # 最大循环次数，防止无限循环
        sub_agents=[content_writer, content_evaluator],
    )


async def run_agent():
    """运行循环Agent演示"""

    # 定义应用和用户信息
    APP_NAME = "cycle_demo"
    USER_ID = "demo_user"

    print("=" * 60)
    print("Cycle Agent 演示 - 迭代内容改进循环")
    print("=" * 60)

    # 创建循环Agent
    cycle_agent = create_agent()

    # 创建Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=cycle_agent, session_service=session_service)

    # 内容创作要求
    user_request = ("Write a professional product description for an AI-powered smart home security system. "
                    "Include key features, benefits, and target audience.")

    print(f"创作要求：{user_request}")
    print("\n迭代改进过程：")

    user_message = Content(parts=[Part.from_text(text=user_request)])

    iteration_count = 0
    current_agent = None

    async for event in runner.run_async(user_id=USER_ID, session_id=str(uuid.uuid4()), new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.function_call and part.function_call.name == "exit_refinement_loop":
                        print(f"\n🔧 工具调用：{part.function_call.name}")
                    elif part.function_response:
                        print(f"📋 工具响应：{part.function_response.response}")
                        print("\n🎉 内容改进完成！")
                    elif part.text:
                        # 检测新的迭代轮次
                        if event.author == "content_writer" and current_agent != "content_writer":
                            iteration_count += 1
                            print(f"\n{'='*20} 第 {iteration_count} 轮迭代 {'='*20}")
                            print(f"[{event.author}] 内容创作：")
                        elif event.author == "content_evaluator":
                            print(f"\n[{event.author}] 质量评估：")
                        else:
                            print(f"[{event.author}]:")

                        print(part.text)
                        print("-" * 40)
                        current_agent = event.author


if __name__ == "__main__":
    print("🔄 Cycle Agent 示例")
    print("展示通过 output_key 和 exit 工具控制的循环改进")
    asyncio.run(run_agent())
