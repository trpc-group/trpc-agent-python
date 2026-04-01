#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""AgentTool 示例 - 将Agent包装成Tool

演示如何使用 AgentTool 将Agent包装成工具，实现Agent间的协作。
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import AgentTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# =============================================================================
# 1. 创建模型配置
# =============================================================================

# 创建共享的模型实例
model = OpenAIModel(
    model_name="deepseek-v3-local-II",
    api_key=os.environ.get("API_KEY", ""),
    base_url="http://v2.open.venus.woa.com/llmproxy",
)

# =============================================================================
# 2. 创建Agent
# =============================================================================


def create_translation_agent():
    """创建专业的翻译Agent"""
    return LlmAgent(
        name="translator",
        model=model,
        description="专业的文本翻译工具",
        instruction="你是一个专业的翻译工具，能够准确翻译中英文文本。保持原文的语调和含义，提供自然流畅的翻译结果。",
    )


def create_main_agent():
    """创建主Agent，使用翻译工具"""

    # 创建翻译Agent
    translator = create_translation_agent()

    # 包装成AgentTool
    translator_tool = AgentTool(agent=translator)

    return LlmAgent(
        name="content_processor",
        model=model,
        description="内容处理助手，可以调用翻译工具处理多语言内容",
        instruction="你是内容处理助手，可以调用翻译工具处理多语言内容。根据用户需求决定是否需要翻译。",
        tools=[translator_tool],
    )


# =============================================================================
# 3. 演示函数
# =============================================================================


async def run_agent_tool_demo():
    """运行AgentTool演示"""

    print("🔧 AgentTool 示例演示")
    print("=" * 60)
    print("本示例展示了如何使用AgentTool实现Agent间协作：")
    print("• 翻译Agent → 翻译工具")
    print("• 主Agent调用翻译工具")
    print("=" * 60)

    # 创建主Agent和Runner
    main_agent = create_main_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="agent_tool_demo", agent=main_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # 测试场景
    test_scenarios = [
        "请将这段中文翻译成英文：人工智能正在改变我们的世界。",
        "Please translate this to Chinese: Hello, how are you today?",
    ]

    for i, query in enumerate(test_scenarios, 1):
        print(f"\n📝 测试 {i}: {query}")
        print("🤖 助手: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
            if event.content and event.content.parts and event.author != "user":
                if event.partial:
                    # 流式输出
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    # 工具调用和结果
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [调用工具: {part.function_call.name}]")
                        elif part.function_response:
                            print(f"📊 [工具结果: {part.function_response.response}]")

        print("\n" + "-" * 50)

    await runner.close()
    print("\n✅ AgentTool 演示完成！")


# =============================================================================
# 4. 主函数
# =============================================================================


async def main():
    """主函数"""
    try:
        await run_agent_tool_demo()

    except KeyboardInterrupt:
        print("\n\n👋 演示被中断")
    except Exception as e:
        print(f"\n❌ 演示过程中出现错误: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
