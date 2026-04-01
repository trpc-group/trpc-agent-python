#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import get_tool
from trpc_agent_sdk.tools import register_tool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# =============================================================================
# 1. 直接包装函数创建工具
# =============================================================================
async def get_weather(city: str) -> dict:
    """获取指定城市的天气信息

    Args:
        city: 城市名称，如"北京"、"上海"等

    Returns:
        包含天气信息的字典，包括温度、天气状况和湿度
    """
    # 模拟天气API调用
    weather_data = {
        "北京": {
            "temperature": "15°C",
            "condition": "晴天",
            "humidity": "45%"
        },
        "上海": {
            "temperature": "18°C",
            "condition": "多云",
            "humidity": "65%"
        },
        "Shenzhen": {
            "temperature": "25°C",
            "condition": "小雨",
            "humidity": "80%"
        },
    }

    if city in weather_data:
        return {"status": "success", "city": city, **weather_data[city], "last_updated": "2024-01-01T12:00:00Z"}
    else:
        return {
            "status": "error",
            "error_message": f"暂不支持查询{city}的天气信息",
            "supported_cities": list(weather_data.keys()),
        }


async def calculate(operation: str, a: float, b: float) -> float:
    """Perform basic mathematical calculations.

    Args:
        operation: The operation to perform (add, subtract, multiply, divide)
        a: First number
        b: Second number

    Returns:
        The result of the calculation
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }

    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")

    result = operations[operation](a, b)
    return result


class City(BaseModel):
    """Address information for weather query."""
    city: str = Field(..., description="City name")


class Address(BaseModel):
    """Address information for weather query."""
    city: City = Field(..., description="City name")
    province: str = Field(..., description="Province name")


class PostalCodeInfo(BaseModel):
    """Address information for weather query."""
    city: str = Field(..., description="City name")
    postal_code: str = Field(..., description="Postal code")


def get_postal_code(addr: Address) -> PostalCodeInfo:
    """Get postal code for the given address."""
    cities = {
        "广东": {
            "Shenzhen": "518000",
            "广州": "518001",
            "珠海": "518002",
        },
        "江苏": {
            "南京": "320000",
            "苏州": "320001",
        }
    }
    return PostalCodeInfo(city=addr.city.city, postal_code=cities.get(addr.province, {}).get(addr.city.city, "Unknown"))


# =============================================================================
# 2. 使用装饰器注册工具
# =============================================================================


@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """获取当前会话信息

    Args:
        tool_context: 执行上下文（自动注入）

    Returns:
        当前会话的基本信息
    """
    # 这里应该是具体的业务逻辑，比如：
    # - 异步数据库查询用户信息
    # - 调用外部API获取用户状态等
    # 此处直接通过tool_context.session获取会话信息

    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }


# =============================================================================
# 3. 创建Agent和演示
# =============================================================================


def create_agent():
    """创建配置了Function Tool的Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # 方式1：直接包装函数创建工具
    weather_tool = FunctionTool(get_weather)
    calculate_tool = FunctionTool(calculate)
    postal_code_tool = FunctionTool(get_postal_code)

    # 方式2：从注册表获取工具
    session_tool = get_tool("get_session_info")

    return LlmAgent(
        name="function_tool_demo_agent",
        description="演示Function Tool两种用法的助手",
        model=model,
        instruction="""你是一个助手，可以查询天气信息和获取会话信息。请根据用户需求选择合适的工具。""",
        tools=[weather_tool, calculate_tool, session_tool, postal_code_tool],
    )


async def run_agent():
    """运行Function Tool演示"""

    print("🔧 Function Tool 示例演示")
    print("=" * 60)
    print("本示例展示了Function Tool的两种用法：")
    print("• 直接包装异步函数创建工具 (get_weather)")
    print("• 装饰器注册异步工具 (get_session_info)")
    print("=" * 60)

    # 创建Agent和Runner
    agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="function_tool_demo", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # 测试查询列表
    test_queries = [
        "帮我查询北京的天气",
        "帮我查询广东深圳的邮编",
        "查看当前会话信息",
        "Now calculate 15 * 3.5",
    ]

    for i, query in enumerate(test_queries, 1):
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
                    # 完整事件
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [调用工具: {part.function_call.name}]")
                            print(f"   参数: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [工具结果: {part.function_response.response}]")

        print("\n" + "-" * 50)

    await runner.close()
    print("\n✅ Function Tool 演示完成！")


# =============================================================================
# 4. 主函数
# =============================================================================


async def main():
    """主函数"""
    try:
        await run_agent()
    except KeyboardInterrupt:
        print("\n\n👋 演示被中断")
    except Exception as e:
        print(f"\n❌ 演示过程中出现错误: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
