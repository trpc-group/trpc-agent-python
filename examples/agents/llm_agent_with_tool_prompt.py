# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

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


def get_weather() -> dict:
    """获取今天的天气"""
    return {"temperature": "25°C", "condition": "晴天", "humidity": "60%"}


def get_tomorrow_weather() -> dict:
    """获取明天的天气"""
    return {"temperature": "21°C", "condition": "阴天", "humidity": "70%"}


def create_weather_agent():
    """创建天气查询Agent，展示LlmAgent的各种能力"""
    return LlmAgent(
        name="weather_agent_with_tool_prompt",
        description="天气查询助手",
        model=OpenAIModel(
            model_name="deepseek-v3-local-II",
            base_url="http://v2.open.venus.woa.com/llmproxy/v1",
            api_key=os.environ.get("API_KEY", ""),
            add_tools_to_prompt=True,
            # 框架提供"xml"和"json"两种注入tool_prompt的方式，如果不填tool_prompt，默认使用"xml"
            # tool_prompt="xml",
        ),
        # 使用状态变量进行模板替换 - 演示 {var} 语法
        instruction="""你是一个天气查询助手，帮助用户查询天气""",
        tools=[FunctionTool(get_weather), FunctionTool(get_tomorrow_weather)],
    )


async def run_agent():
    """运行天气查询演示，展示状态变量的使用"""

    app_name = "weather_with_tool_prompt_demo"

    # 创建Agent和Runner
    agent = create_weather_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"

    # 演示查询列表
    query = "今明两天的天气怎么样？"
    # 每次查询使用新的session
    current_session_id = str(uuid.uuid4())

    # for query in ["今天天气怎么样？", "今天和明天天气怎么样？"]:
    print(f"🆔 Session ID: {current_session_id[:8]}...")
    print(f"📝 用户: {query}")

    user_content = Content(parts=[Part.from_text(text=query)])

    print("🤖 助手: ", end="", flush=True)

    async for event in runner.run_async(
            user_id=user_id,
            session_id=current_session_id,
            new_message=user_content,
    ):
        if event.content is None:
            continue
        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue
        for part in event.content.parts:
            if part.function_call:
                print(f"\n🔧 [调用工具: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [工具结果: {part.function_response.response}]")
            # 取消注释，可以获得LLM完整的text输出
            # elif part.text:
            #     print(f"\n✅ {part.text}")


if __name__ == "__main__":
    print("-" * 40)
    asyncio.run(run_agent())
    print("\n" + "-" * 40)
