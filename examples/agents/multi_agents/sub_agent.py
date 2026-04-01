#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Sub Agents 示例 - 智能路由系统

演示层次化Agent结构：协调员 → 专业Agent
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


async def get_product_info(product_type: str) -> str:
    """获取产品信息的工具函数"""
    products = {
        "speakers": "Smart Speaker Pro - Voice control, AI assistant - $199",
        "displays": "Smart Display 10 - Touch screen, video calls - $399",
        "security": "Home Security System - 24/7 monitoring, mobile alerts - $599"
    }
    return products.get(product_type, f"Product type '{product_type}' not found")


async def generate_consult_id() -> str:
    """Generate a unique consultation ID. When a customer contacts us, we must generate a unique consultation ID."""
    return str(uuid.uuid4())


async def check_system_status(device: str) -> str:
    """检查系统状态的工具函数"""
    return f"System diagnostic for {device}: Status OK, all functions normal"


def create_agent():
    """创建带有子Agent的智能客服系统"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # 技术支持专员
    technical_support_agent = LlmAgent(
        name="technical_support",
        model=model,
        description="Technical support specialist",
        instruction="""You are a technical support specialist.
Help with device troubleshooting and system diagnostics.
Use check_system_status tool to check device status.""",
        tools=[FunctionTool(check_system_status)],
        disallow_transfer_to_parent=True,
        output_key="technical_result",
        # 如果不需要前面Agent的输出，可以将此参数设为False（默认True）
        include_previous_history=False,
    )

    # 销售咨询专员
    sales_consultant_agent = LlmAgent(
        name="sales_consultant",
        model=model,
        description="Sales consultant for product information",
        instruction="""You are a sales consultant. Help customers with product information.
Use get_product_info tool with: speakers, displays, or security.""",
        tools=[FunctionTool(get_product_info)],
        disallow_transfer_to_parent=True,
        output_key="sales_result",
    )

    # 主客服协调员
    customer_service_coordinator = LlmAgent(
        name="customer_service_coordinator",
        model=model,
        description="Customer service coordinator that routes inquiries",
        instruction="""You are a customer service coordinator.
First you should invoke generate_consult_id tool to generate a unique consultation ID.
And then Route customer inquiries:
- Technical issues → transfer to technical_support
- Product questions → transfer to sales_consultant""",
        sub_agents=[technical_support_agent, sales_consultant_agent],
        output_key="coordinator_result",
        tools=[FunctionTool(generate_consult_id)],
    )

    return customer_service_coordinator


async def run_agent():
    """运行子Agent演示"""

    # 定义应用和用户信息
    APP_NAME = "subagent_demo"
    USER_ID = "demo_customer"

    print("=" * 40)
    print("Sub Agents 演示 - 智能路由")
    print("=" * 40)

    # 创建客服系统
    customer_service = create_agent()

    # 创建Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=customer_service, session_service=session_service)

    # 测试场景
    test_scenarios = [
        {
            "title": "Technical Support",
            "query": "My speaker stopped working. Can you help?",
            "session_id": str(uuid.uuid4()),
        },
        {
            "title": "Sales Inquiry",
            "query": "What security systems do you have?",
            "session_id": str(uuid.uuid4()),
        },
    ]

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n场景 {i}: {scenario['title']}")
        print(f"问题: {scenario['query']}")
        print("\n处理过程:")

        user_message = Content(parts=[Part.from_text(text=scenario["query"])])

        async for event in runner.run_async(user_id=USER_ID,
                                            session_id=scenario["session_id"],
                                            new_message=user_message):
            if event.content and event.content.parts and event.author != "user":
                if not event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(f"[{event.author}] {part.text}")
                        if part.function_call:
                            print(f"🔧 [{event.author}] Tool call: {part.function_call.name} "
                                  f"with arguments: {part.function_call.args}")
                        elif part.function_response:
                            print(f"🔧 [{event.author}] Tool response: {part.function_response.response}")

        print("-" * 40)


if __name__ == "__main__":
    print("🏢 Sub Agents 示例")
    print("展示智能路由：协调员 → 专业Agent")
    asyncio.run(run_agent())
