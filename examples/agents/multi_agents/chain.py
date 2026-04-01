#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Chain Agent 示例 - 顺序执行的文档处理流水线

演示如何使用 ChainAgent 通过 output_key 在Agent间传递信息：
文档提取 → 内容翻译 → 格式优化
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import ChainAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent():
    """创建文档处理的链式Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # Step 1: 内容提取Agent
    extractor_agent = LlmAgent(
        name="content_extractor",
        model=model,
        description="Extract key information from input text",
        instruction="Extract key information from the input text and structure it clearly. "
        "Focus on main points, features, and target audience.",
        output_key="extracted_content",  # 将输出保存到状态变量
    )

    # Step 2: 翻译Agent，引用前一个Agent的输出
    translator_agent = LlmAgent(
        name="translator",
        model=model,
        description="Translate content to English with professional formatting",
        instruction=
        """Translate the following extracted content to English while maintaining the original meaning and structure:

{extracted_content}

Provide a natural, professional English translation with proper formatting:
- Use clear headings and organized sections
- Apply professional document structure
- Include bullet points where appropriate
- Ensure readability and professional presentation""",
        output_key="translated_content",  # 将翻译结果保存到状态变量
    )

    # 创建链式Agent - 确定性顺序执行
    # Chain Agent始终按照sub_agents列表的顺序执行，无论输入如何
    # 通过output_key在各个Agent间传递状态，实现数据流水线处理
    return ChainAgent(
        name="document_processor",
        description="Sequential document processing: extract → translate",
        sub_agents=[extractor_agent, translator_agent],
    )


async def run_agent():
    """运行链式Agent演示"""

    # 定义应用和用户信息
    APP_NAME = "chain_demo"
    USER_ID = "demo_user"

    print("=" * 60)
    print("Chain Agent 演示 - 通过 output_key 传递信息")
    print("=" * 60)

    # 创建链式Agent
    chain_agent = create_agent()

    # 创建Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=chain_agent, session_service=session_service)

    # 测试文本（中文产品介绍）
    test_content = """
    产品名称：智能家居控制系统
    主要功能：语音控制、远程监控、自动化场景设置
    技术特点：支持多种设备接入，AI智能学习用户习惯，云端数据同步
    目标用户：追求便利生活的现代家庭，技术爱好者
    价格：999元起
    """

    print(f"输入内容：{test_content}")
    print("\n处理流程：提取 → 翻译")

    user_message = Content(parts=[Part.from_text(text=test_content)])

    async for event in runner.run_async(user_id=USER_ID, session_id=str(uuid.uuid4()), new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(f"\n[{event.author}] 输出：")
                        print(part.text)
                        print("-" * 40)


if __name__ == "__main__":
    print("🔗 Chain Agent 示例")
    print("展示通过 output_key 在Agent间传递信息")
    asyncio.run(run_agent())
