#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid
from typing import Annotated
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.agents import langgraph_llm_node
from trpc_agent_sdk.agents import langgraph_tool_node
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# 定义状态结构
class State(TypedDict):
    messages: Annotated[list, add_messages]


# 定义工具函数
@tool
@langgraph_tool_node
def calculate(operation: str, a: float, b: float) -> str:
    """执行基础数学运算

    Args:
        operation: 运算类型 ('add', 'subtract', 'multiply', 'divide')
        a: 第一个数字
        b: 第二个数字
    """
    try:
        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            if b == 0:
                return "错误：不能除以零"
            result = a / b
        else:
            return f"错误：未知运算 '{operation}'"

        return f"计算结果：{a} {operation} {b} = {result}"
    except Exception as e:  # pylint: disable=broad-except
        return f"计算错误：{str(e)}"


def build_graph():
    """构建简单的LangGraph，演示基础功能"""

    # 初始化模型
    model = init_chat_model(
        "deepseek:deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        api_base="http://v2.open.venus.woa.com/llmproxy",
    )
    tools = [calculate]
    llm_with_tools = model.bind_tools(tools)

    # 定义LLM节点 - 使用@llm_node装饰器自动记录LLM调用
    @langgraph_llm_node
    def chatbot(state: State):
        """聊天机器人节点，可以使用工具"""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # tool_node和llm_node都可以通过RunConfig获取Agent上下文，如下所示：
    # from langchain_core.runnables.config import RunnableConfig
    # from trpc_agent_sdk.agents import get_langgraph_agent_context

    # @langgraph_llm_node
    # def chatbot(state: State, config: RunnableConfig):
    #     """聊天机器人节点，可以使用工具"""
    #     ctx = get_langgraph_agent_context(config)

    # 构建图
    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)

    # 添加工具节点
    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)

    # 添加边
    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")

    return graph_builder.compile()


def create_agent():
    """创建简单的LangGraph Agent"""
    graph = build_graph()

    return LangGraphAgent(
        name="simple_langgraph_agent",
        description="一个数学计算助手，支持加减乘除运算",
        graph=graph,
        instruction="""
你是一个友好的助手。

你可以帮助用户进行：
1. 日常对话
2. 数学计算（使用calculate工具）

请保持友好、专业的语调。""",
    )


async def run_agent():
    """运行基础演示"""
    print("🤖 LangGraph Agent 示例")
    print("=" * 40)

    agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="langgraph_demo", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    queries = ["你好，我是张三", "帮我计算 15 * 23 等于多少？", "谢谢！"]

    for query in queries:
        print(f"\n👤 用户: {query}")
        print("🤖 助手: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        # from trpc_agent_sdk.configs import RunConfig
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
            # 可以传通过RunConfig配置LangGraph的运行时配置
            # run_config=RunConfig(
            #     agent_run_config={
            #         "input": {"user_input": {"custom1": "xxx"}},
            #         # "stream_mode": ["values"],
            #         # "runnable_config": {"configurable": {"xxx": "xxx"}},
            #     },
            # ),
        ):
            # 可以获取LangGraph的原始响应数据，如下所示：
            # from trpc_agent_sdk.agents.langgraph_agent import get_langgraph_payload
            # payload = get_langgraph_payload(event)
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
            else:
                for part in event.content.parts:
                    if part.function_call:
                        print(f"\n🔧 [调用工具: {part.function_call.name}({part.function_call.args})]")
                    elif part.function_response:
                        print(f"\n📊 [工具结果: {part.function_response.response}]")

        print()

    await runner.close()


async def main():
    """主函数"""
    try:
        await run_agent()
    except Exception as e:
        print(f"\n❌ 出现错误: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
