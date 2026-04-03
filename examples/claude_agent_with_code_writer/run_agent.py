# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Claude Agent 代码生成示例 """

import asyncio
import json
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_code_writer_agent():
    """Run the Claude code writer agent demo"""

    app_name = "claude_code_writing_app"

    from agent.agent import create_agent, setup_claude, cleanup_claude

    # 初始化 Claude 环境：启动 Anthropic Proxy Server 子进程
    setup_claude()

    # 创建 Agent 并初始化运行时（AsyncRuntime + SessionManager）
    agent = create_agent()
    agent.initialize()

    # 创建内存会话服务和 Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "Write a Python function that calculates the Fibonacci sequence up to n terms, save it to 'fibonacci.py'.",
    ]

    try:
        for query in demo_queries:
            # 每个查询创建独立的 session
            current_session_id = str(uuid.uuid4())

            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=current_session_id,
                state={
                    "user_name": f"{user_id}",
                },
            )

            print(f"🆔 Session ID: {current_session_id[:8]}...")
            print(f"📝 User: {query}")

            user_content = Content(parts=[Part.from_text(text=query)])

            print("🤖 Assistant: ", end="", flush=True)
            # 异步迭代 Agent 返回的事件流
            async for event in runner.run_async(user_id=user_id,
                                                session_id=current_session_id,
                                                new_message=user_content):
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

    finally:
        # 资源清理：关闭 Runner -> 销毁 Agent（停止 Runtime）-> 停止 Proxy 子进程
        await runner.close()
        agent.destroy()
        cleanup_claude()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    asyncio.run(run_code_writer_agent())
