# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.planners import BuiltInPlanner
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import ThinkingConfig


def get_weather(city: str) -> dict:
    """获取指定城市的天气信息"""
    return {"temperature": "25°C", "condition": "晴天", "humidity": "60%"}


def create_weather_agent(model_name):
    """创建天气查询Agent，展示LlmAgent的各种能力"""

    # 创建工具
    weather_tool = FunctionTool(get_weather)

    # 创建LlmAgent
    return LlmAgent(
        name="weather_agent",
        description="专业的天气查询助手，能够提供实时天气和预报信息",
        model=OpenAIModel(
            model_name=model_name,
            api_key=os.environ.get("API_KEY", ""),
            base_url="http://v2.open.venus.woa.com/llmproxy",
            # 有两种场景，开启add_tools_to_prompt能提高Agent的生成效果:
            # 1. 当thinking模型不支持工具调用时，可以启用ToolPrompt框架从LLM文本中解析工具调用的能力
            # 2. 当thinking模型在思考过程中调用工具时，LLM模型服务无法返回工具调用的json，此时也可以启用ToolPrompt，
            #     能促使LLM模型在正文中输出工具调用的特殊文本，以提高工具调用的概率；
            # 你可以取消下面的注释，以使用ToolPrompt
            # add_tools_to_prompt=True,
        ),
        instruction="""你是一个天气查询助手，回答用户问题""",
        tools=[weather_tool],
        # 注意：thinking_budget需要小于max_output_tokens
        generate_content_config=GenerateContentConfig(max_output_tokens=10240, ),
        # 模型必须是思考模型，才能使用此Planner，非思考模型此项配置将不会生效
        planner=BuiltInPlanner(thinking_config=ThinkingConfig(
            include_thoughts=True,
            thinking_budget=2048,
        ), ),
    )


async def run_weather_demo(model_name):
    """运行天气查询演示，展示状态变量的使用"""

    app_name = "weather_demo"

    # 创建Agent和Runner
    agent = create_weather_agent(model_name)
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    query = "北京的天气怎么样？"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 用户: {query}")

    user_content = Content(parts=[Part.from_text(text=query)])

    print("🤖 助手: ", end="", flush=True)
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        # 检查event.content是否存在
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            # 跳过思考部分，partial=True时已经输出了
            if part.thought:
                continue
            if part.function_call:
                print(f"\n🔧 [调用工具: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [工具结果: {part.function_response.response}]")
            # 取消注释，可以获得LLM完整的text输出
            # elif part.text:
            #     print(f"\n✅ {part.text}")

    print("\n" + "-" * 40)


async def main():
    models = ["glm-4.5-fp8"]
    for name in models:
        await run_weather_demo(name)


if __name__ == "__main__":
    asyncio.run(main())
