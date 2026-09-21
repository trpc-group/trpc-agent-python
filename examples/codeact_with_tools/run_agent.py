# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

# Load environment variables from this example's .env file.
load_dotenv(Path(__file__).with_name(".env"))


async def run_agent_demo(
    *,
    title: str,
    query: str,
    expected: str,
    agent: LlmAgent,
) -> None:
    """Run one CodeAct-related feature demonstration."""

    app_name = f"codeact_demo_{agent.name}"

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"

    print(f"\n📌 Demo: {title}")
    print(f"🤖 Agent: {agent.name}")
    print(
        f"🔧 Mode: "
        f"{type(agent.code_act).__name__ if agent.code_act else 'CodeActTool'}"
    )
    print(f"🎯 Expected: {expected}")

    session_id = str(uuid.uuid4())

    print(f"📝 User: {query}")

    user_content = Content(parts=[Part.from_text(text=query)])

    print("🤖 Assistant: ", end="", flush=True)
    printed_partial = False
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                printed_partial = True
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                if event.object == "codeact.result" and part.text:
                    print(f"\n✅ [Final CodeAct Result]\n{part.text}")
                elif part.function_call:
                    print(
                        f"\n🔧 [Invoke Tool: "
                        f"{part.function_call.name}({part.function_call.args})]"
                    )
                elif part.function_response:
                    print(
                        f"\n📦 [Tool Response: "
                        f"{part.function_response.response}]"
                    )
                elif part.executable_code:
                    print(
                        f"\n💻 [Executable Code]\n"
                        f"```python\n{part.executable_code.code}\n```"
                    )
                elif part.code_execution_result:
                    print(
                        f"\n✅ [Code Execution Result]\n"
                        f"```\n{part.code_execution_result.output}\n```"
                    )
                elif part.text and not printed_partial:
                    print(f"\n✅ [Final Answer]\n{part.text}")
    finally:
        await runner.close()

    print("\n" + "-" * 40)


async def main():
    from agent.agent import root_agent

    await run_agent_demo(
        title="CodeAct 持久变量、ObjectRef 与 self 工具调用",
        query=(
            "加载前 100000 个自然数，计算其中所有能被 7 整除数字的平方和。"
            "请严格复用第一轮加载的 nums，不要再次加载数据。"
        ),
        expected="value = 47616904735715",
        agent=root_agent,
    )


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 TRPC Agent CodeAct Tools Example")
    print("=" * 60)
    print()
    asyncio.run(main())
