#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# =============================================================================
# 1. 创建天气工具集
# =============================================================================


class WeatherToolSet(BaseToolSet):
    """天气工具集，包含天气查询相关的所有工具"""

    def __init__(self):
        super().__init__()
        self.name = "weather_toolset"
        self.tools = []

    @override
    def initialize(self) -> None:
        """初始化工具集，创建所有天气相关工具"""
        super().initialize()
        self.tools = [
            FunctionTool(self.get_current_weather),
            FunctionTool(self.get_weather_forecast),
        ]

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """根据用户权限动态返回可用工具"""
        if not invocation_context:
            return self.tools[:1]  # 无上下文时只返回基础功能

        # 根据用户类型筛选工具
        user_type = invocation_context.session.state.get("user_type", "basic")

        if user_type == "vip":
            return self.tools  # VIP用户可以使用所有工具
        else:
            return self.tools[:1]  # 普通用户只能查看当前天气

    @override
    async def close(self) -> None:
        """清理资源"""
        # 这里可以添加清理逻辑，比如关闭数据库连接
        pass

    # 工具方法
    async def get_current_weather(self, city: str) -> dict:
        """获取指定城市的当前天气

        Args:
            city: 城市名称，如"北京"、"上海"等

        Returns:
            当前天气信息
        """
        # 模拟天气数据
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
            return {"status": "success", "city": city, **weather_data[city], "timestamp": "2024-01-01T12:00:00Z"}
        else:
            return {
                "status": "error",
                "error_message": f"暂不支持查询{city}的天气信息",
                "supported_cities": list(weather_data.keys()),
            }

    async def get_weather_forecast(self, city: str, days: int = 3) -> dict:
        """获取指定城市的天气预报

        Args:
            city: 城市名称
            days: 预报天数，默认3天

        Returns:
            天气预报信息
        """
        # 模拟预报数据
        return {
            "status":
            "success",
            "city":
            city,
            "forecast_days":
            days,
            "forecast": [{
                "date": f"2024-01-{i+1:02d}",
                "temperature": f"{20+i}°C",
                "condition": "晴天"
            } for i in range(days)],
        }


# =============================================================================
# 2. 创建演示Agent
# =============================================================================


def create_agent():
    """创建配置了天气ToolSet的Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # 获取注册的工具集
    weather_toolset = WeatherToolSet()

    # 初始化工具集
    if weather_toolset:
        weather_toolset.initialize()

    return LlmAgent(
        name="weather_toolset_agent",
        description="演示天气ToolSet用法的助手",
        model=model,
        instruction="你是一个天气助手，根据用户需求选择合适的工具，并提供友好的回复。",
        tools=[weather_toolset],
    )


async def run_agent():
    """运行天气ToolSet演示"""

    print("=" * 60)
    print("🔧 天气ToolSet 示例演示")
    print("=" * 60)

    # 创建Agent和Runner
    agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="weather_toolset_demo", agent=agent, session_service=session_service)

    # 模拟不同类型用户的测试
    test_scenarios = [
        {
            "user_id": "basic_user",
            "user_type": "basic",
            "queries": [
                "查询北京的当前天气",
                "获取北京未来5天的天气预报",
            ],
        },
        {
            "user_id": "vip_user",
            "user_type": "vip",
            "queries": [
                "获取北京未来5天的天气预报",
            ],
        },
    ]

    for scenario in test_scenarios:
        user_id = scenario["user_id"]
        user_type = scenario["user_type"]
        session_id = str(uuid.uuid4())

        print(f"\n👤 用户类型: {user_type.upper()}")
        print("=" * 40)

        # 创建 Session 时直接设置用户状态
        await session_service.create_session(
            app_name="weather_toolset_demo",
            user_id=user_id,
            session_id=session_id,
            state={"user_type": user_type},
        )

        # 执行测试查询
        for i, query in enumerate(scenario["queries"], 1):
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

            print("\n" + "-" * 30)

    # 这里需要主动 close
    await runner.close()
    print("\n✅ 天气ToolSet 演示完成！")


# =============================================================================
# 3. 主函数
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
