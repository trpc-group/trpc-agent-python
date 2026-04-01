# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part
# Load environment variables from the .env file
load_dotenv()


async def run_user_history_demo():
    """运行用户历史记录注入演示。

    演示如何通过 HistoryRecord 将用户历史对话注入到 Agent 上下文中，
    使 Agent 能够基于历史信息回答问题，而无需额外调用工具。
    """

    app_name = "user_history_demo"

    from agent.agent import root_agent
    from agent.tools import make_user_history_record

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    # 多轮对话共用同一个 session_id，使 Agent 能看到本次会话的历史消息
    session_id = str(uuid.uuid4())

    demo_queries = [
        "What's your name?",
        "what is the weather like in paris?",
        "Do you remember my name?",
    ]

    for query in demo_queries:
        print(f"📝 用户: {query}")

        # 构造用户历史记录，并根据当前 query 构建上下文内容
        # history_content 会包含与 query 相关的历史问答对，供 Agent 参考
        history_record = make_user_history_record()
        history_content = history_record.build_content(query)
        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 助手: ", end="", flush=True)
        # 开启会话历史保存，后续轮次可以累积上下文
        run_config = RunConfig(save_history_enabled=True)
        # new_message 传入列表 [history_content, user_content]，
        # 将历史记录和用户当前提问一起注入到 Agent 的输入中
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=[history_content, user_content],
            run_config=run_config,
        ):
            # 跳过空内容事件
            if not event.content or not event.content.parts:
                continue

            # partial=True 表示流式输出的中间片段，实时打印文本
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # 完整事件：打印工具调用和工具返回结果，跳过思考过程
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_user_history_demo())
