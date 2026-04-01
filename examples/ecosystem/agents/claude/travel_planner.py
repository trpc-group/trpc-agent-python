# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#

import asyncio
import datetime
import json
import os
import uuid

from claude_agent_sdk.types import ClaudeAgentOptions
from mcp import StdioServerParameters
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools.mcp_tool import StdioConnectionParams
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# 工具环境安装请参考: https://knot.woa.com/mcp/detail/39
# 1. (可选)安装uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. 安装mcp: uv pip install duckduckgo-mcp-server
class DuckDuckGoSearchMCP(MCPToolset):
    """DuckDuckGoSearchMCP 搜索工具集"""

    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=StdioServerParameters(
                # 服务器启动执行的命令
                command="uvx",
                # 运行的参数
                args=["duckduckgo-mcp-server"],
                # 环境变量，默认为 None，表示使用当前环境变量
                env=None,
            ),
            timeout=10.0,  # 10s timeout
        )


def get_current_date():
    """获取今天的日期，日期格式为：2025-01-01"""
    return datetime.datetime.now().strftime("%Y-%m-%d")


def create_agent():
    """Create a ClaudeAgent with weather tool"""

    search_tools = DuckDuckGoSearchMCP()

    return ClaudeAgent(
        name="travel_planner",
        description="旅游规划助手",
        model=OpenAIModel(
            model_name="deepseek-v3-local-II",
            api_key=os.environ.get("API_KEY", ""),
            base_url="http://v2.open.venus.woa.com/llmproxy",
        ),
        instruction="""
你是一个旅游规划助手，能够根据用户的需求进行旅游规划，请你综合考虑交通方式、住宿、饮食、景点、购物、娱乐等各方面因素，给出最合理的旅游规划。
如果用户没有提日期，请你获得今天的日期，然后给出从当前日期出发，查看机票、酒店等价格，以及当前季节适合的景点，并给出最佳的（考虑时间和性价比）的旅游规划路线。
你不需要一次性给出完整的旅游规划，你可以分步给出旅游规划。
搜索工具调用并发为2。
""",
        claude_agent_options=ClaudeAgentOptions(
            # Claude-Code内置Tools
            allowed_tools=["TodoWrite"], ),
        # 业务自定义Tools
        tools=[
            FunctionTool(get_current_date),
            search_tools,
        ],
    )


async def run_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    user_input: str,
):
    """Run Agent"""
    print(f"📝 用户: {user_input}")

    user_content = Content(parts=[Part.from_text(text=user_input)])

    print("\n🤖 Agent: ", end="", flush=True)
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        # Check if event.content exists
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.function_call:
                args_str = json.dumps(part.function_call.args, ensure_ascii=False)[:200]
                print(f"\n🔧 [Tool Call: {part.function_call.name}({args_str})]", flush=True)
            elif part.function_response:
                response_str = json.dumps(part.function_response.response, ensure_ascii=False)[:200]
                print(f"📊 [Tool Result: {part.function_response.name}({response_str})]", flush=True)
            # uncomment part.text to get the full text
            # elif part.text:
            #     print(f"\n[🤖 Agent:] {part.text}", flush=True)
    print("", flush=True)


async def main(agent: ClaudeAgent):
    """Main function to run Claude Agent"""

    # Initialize session variables in main
    app_name = "claude_travel_planner"
    user_id = "Alice"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"👤 User ID: {user_id}")
    print("\n💬 请输入您的旅游需求（输入 'quit' 或 'exit' 退出）: ")

    try:
        # Interactive loop to get user input
        while True:
            try:
                user_input = input("> ")

                if user_input.strip().lower() in ["quit", "exit", "q"]:
                    print("👋 再见！")
                    break

                if not user_input.strip():
                    continue

                await run_agent(runner, user_id, session_id, user_input)

            except EOFError:
                print("\n👋 再见！")
                break
            except KeyboardInterrupt:
                print("\n\n👋 再见！")
                break

    finally:
        # Clean up
        await runner.close()


if __name__ == "__main__":
    # 设置Claude-Code默认调用的模型
    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )
    # 设置proxy的host:port，初始化Claude Proxy Server进程
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8082, claude_models={"all": model})  # Maps to sonnet, opus, haiku

    # 创建ClaudeAgent并初始化
    agent = create_agent()
    agent.initialize()

    try:
        asyncio.run(main(agent))
    finally:
        # 进程退出需要销毁资源
        agent.destroy()
        destroy_claude_env()
        print("🧹 Claude environment cleaned up")
