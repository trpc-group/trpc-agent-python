#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid
from typing import Any

from langchain_tavily import TavilySearch
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# =============================================================================
# 1. LangChain Tavily 工具封装为 FunctionTool
# 参考文档: https://python.langchain.com/docs/integrations/tools/tavily_search/
# =============================================================================
async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """使用 Tavily 搜索指定查询并返回结果。

    环境变量:
        - TAVILY_API_KEY: Tavily 的 API Key（必需）。

    Args:
        query: 搜索的查询文本
        max_results: 返回的结果数量上限

    Returns:
        包含原始搜索结果的字典。
    """

    try:
        tool = TavilySearch(max_results=max_results)
        res = await tool.ainvoke(query)
        # 兼容不同返回结构
        if isinstance(res, dict) and "results" in res:
            items = res["results"]
        elif isinstance(res, list):
            items = res
        else:
            items = []
        return {
            "status": "success",
            "query": query,
            "result_count": len(items),
            "results": items,
        }
    except Exception as e:  # pylint: disable=broad-except
        return {"status": "error", "error_message": str(e)}


# =============================================================================
# 2. 创建 Agent 与演示 Runner
# =============================================================================


def create_agent() -> LlmAgent:
    """创建配置了 Tavily 搜索工具的 Agent。"""

    # 使用 OpenAIModel 作为示例（也可替换为你的实际模型）
    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    tavily_tool = FunctionTool(tavily_search)

    return LlmAgent(
        name="langchain_tavily_agent",
        description="集成 LangChain Tavily 搜索工具的示例助手",
        model=model,
        instruction=("你可以使用 Tavily 搜索引擎工具来检索实时信息。"
                     "当用户提问需要上网搜索时，优先调用 tavily_search 工具并基于返回结果作答。"),
        tools=[tavily_tool],
    )


async def run_agent():
    """运行 Tavily 工具演示。"""

    print("🔎 LangChain Tavily 工具 示例演示")
    print("=" * 60)
    print("本示例展示如何将 LangChain 的 TavilySearchResults 封装为 FunctionTool。")
    print("环境要求: 需设置 TAVILY_API_KEY 为你的 Tavily API Key")
    print("=" * 60)

    # 创建 Agent 和 Runner
    agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name="langchain_tavily_demo", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # 测试查询列表
    test_queries = [
        "帮我搜索一下今天 AI 领域的重大新闻",
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
    print("\n✅ Tavily 工具 演示完成！")


# =============================================================================
# 3. 主函数
# =============================================================================


async def main():
    """主函数"""
    # 关键环境变量提示
    if not os.environ.get("TAVILY_API_KEY"):
        print("⚠️ 未检测到 TAVILY_API_KEY，请先在环境变量中配置 Tavily API Key。")
    try:
        await run_agent()
    except KeyboardInterrupt:
        print("\n\n👋 演示被中断")
    except Exception as e:
        print(f"\n❌ 演示过程中出现错误: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
