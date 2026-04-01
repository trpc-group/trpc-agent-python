#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""MCP Tools 示例 - 使用Model Context Protocol工具

演示如何在trpc_agent中使用MCP协议连接外部工具服务器。
本示例展示了：
1. 创建简单的MCP服务器
2. 使用MCPToolset连接MCP服务器
3. Agent调用MCP工具
"""

import asyncio
import os
import sys
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import SseConnectionParams
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# =============================================================================
# 1. 创建自定义 stdio MCPToolset
# =============================================================================
class StdioMCPToolset(MCPToolset):
    """stdio MCP工具集"""

    def __init__(self):
        super().__init__()

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"/usr/local/Python{sys.version_info.major}{sys.version_info.minor}/lib/:" + env.get(
            "LD_LIBRARY_PATH", "")
        svr_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_server.py"))
        print(f"svr_file {svr_file}")
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",
            args=[svr_file],
            env=env,
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,
        )
        # 通常一个 mcp server 会提供多个工具，需要通过 tool_filter 过滤出需要的工具, 默认是不过滤，即调用所有工具。
        # self._tool_filter = ["get_weather", "calculate"]


class SseMCPToolset(MCPToolset):
    """sse MCP工具集"""

    def __init__(self):
        super().__init__()

        self._connection_params = SseConnectionParams(
            # 必填，用户根据实际地址替换url
            url="http://localhost:8000/sse",
            # 可选的 HTTP 头
            headers={"Authorization": "Bearer token"},
            # 超时时间，单位秒
            timeout=5,
            # SSE 读取超时时间，单位秒
            sse_read_timeout=60 * 5,
        )


class StreamableHttpMCPToolset(MCPToolset):
    """streamable-http MCP工具集"""

    def __init__(self):
        super().__init__()

        self._connection_params = StreamableHTTPConnectionParams(
            # 必填，用户根据实际地址替换url
            url="http://localhost:8000/mcp",
            # 可选的 HTTP 头
            headers={"Authorization": "Bearer token"},
            # 可选的超时时间，单位秒
            timeout=5,
            # 可选的 SSE 读取超时时间，单位秒
            sse_read_timeout=60 * 5,
            # 可选的关闭客户端会话，默认是 True
            terminate_on_close=True,
        )


# =============================================================================
# 2. 创建Agent
# =============================================================================
def create_agent(mcp_toolset: MCPToolset):
    """创建配置了MCP工具的Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    return LlmAgent(
        name="mcp_demo_agent",
        description="演示MCP工具用法的助手",
        model=model,
        instruction="你是一个助手，可以查询天气信息和执行数学计算。请根据用户需求选择合适的工具。",
        tools=[mcp_toolset],
    )


# =============================================================================
# 3. 演示函数
# =============================================================================
async def run_agent():
    """运行MCP工具演示"""

    print("🔧 MCP Tools 示例演示")
    print("=" * 60)
    print("本示例展示了如何使用MCP协议集成外部工具：")
    print("• 创建简单的MCP服务器")
    print("• 使用MCPToolset连接MCP服务器")
    print("• Agent调用MCP工具")
    print("=" * 60)

    # 创建MCP工具集
    mcp_toolset = StdioMCPToolset()
    # mcp_toolset = SseMCPToolset()
    # mcp_toolset = StreamableHttpMCPToolset()

    # 创建Agent和Runner
    agent = create_agent(mcp_toolset)
    session_service = InMemorySessionService()
    runner = Runner(app_name="mcp_demo", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # 测试场景
    test_queries = [
        "查询北京的天气情况",
        "计算 15 乘以 3.5 的结果",
        "上海的天气怎么样？",
        "帮我算一下 100 除以 4",
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
                    # 工具调用和结果
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [调用MCP工具: {part.function_call.name}]")
                            print(f"   参数: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [MCP工具结果: {part.function_response.response}]")

        print("\n" + "-" * 50)

    await runner.close()

    print("\n✅ MCP Tools 演示完成！")


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
