# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Claude Agent 旅游规划示例 """

import asyncio
import json
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_travel_planner_agent():
    """Run the Claude travel planner agent demo"""

    app_name = "claude_travel_planner"

    from agent.agent import create_agent, setup_claude, cleanup_claude

    # 初始化 Claude 环境：启动 Anthropic Proxy Server 子进程
    setup_claude()

    # 创建 Agent 并初始化运行时（AsyncRuntime + SessionManager）
    agent = create_agent()
    agent.initialize()

    # 创建内存会话服务和 Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "Alice"
    session_id = str(uuid.uuid4())

    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"👤 User ID: {user_id}")
    print("\n💬 请输入您的旅游需求（输入 'quit' 或 'exit' 退出）: ")

    try:
        while True:
            try:
                user_input = input("> ")

                if user_input.strip().lower() in ["quit", "exit", "q"]:
                    print("👋 再见！")
                    break

                if not user_input.strip():
                    continue

                print(f"📝 用户: {user_input}")

                user_content = Content(parts=[Part.from_text(text=user_input)])

                print("\n🤖 Agent: ", end="", flush=True)
                async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
                    if not event.content or not event.content.parts:
                        continue

                    # 流式文本片段（partial=True），逐字打印
                    if event.partial:
                        for part in event.content.parts:
                            if part.text:
                                print(part.text, end="", flush=True)
                        continue

                    # 完整事件：工具调用、工具结果、最终响应等
                    for part in event.content.parts:
                        if part.thought:
                            continue
                        if part.function_call:
                            args_str = json.dumps(part.function_call.args, ensure_ascii=False)[:200]
                            print(f"\n🔧 [Tool Call: {part.function_call.name}({args_str})]", flush=True)
                        elif part.function_response:
                            response_str = json.dumps(part.function_response.response, ensure_ascii=False)[:200]
                            print(f"📊 [Tool Result: {part.function_response.name}({response_str})]", flush=True)

                print("\n" + "-" * 40)

            except EOFError:
                print("\n👋 再见！")
                break
            except KeyboardInterrupt:
                print("\n\n👋 再见！")
                break

    finally:
        # 资源清理：关闭 Runner -> 销毁 Agent（停止 Runtime）-> 停止 Proxy 子进程
        await runner.close()
        agent.destroy()
        cleanup_claude()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    asyncio.run(run_travel_planner_agent())
