# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import HistoryRecord
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def make_user_history_record() -> HistoryRecord:
    """设置用户历史记录"""
    record: dict[str, str] = {
        "What's your name?":
        "My name is Alice",
        "what is the weather like in paris?":
        "The weather in Paris is sunny with a pleasant temperature of 25 degrees Celsius. Enjoy the sunshine if you're there!",
        "Do you remember my name?":
        "It seems I don't have your name stored in my memory. Could you remind me what your name is? I can remember it for future conversations if you'd like!",
    }

    history_record = HistoryRecord()
    for query, answer in record.items():
        history_record.add_record(query, answer)
    return history_record


def create_assistant_agent():
    """展示LlmAgent的问答能力"""

    # 创建LlmAgent，展示各种配置能力
    return LlmAgent(
        name="assistant_agent",
        description="普通的问答助手",
        model=OpenAIModel(
            model_name="deepseek-v3-local-II",
            api_key=os.environ.get("API_KEY", ""),
            base_url="http://v2.open.venus.woa.com/llmproxy",
        ),  # type: ignore
        instruction="""你是一个问答助手
**你的任务：**
- 理解提问，并给出友好回答
- 如果可以从历史会话中查询相关的数据，优先从历史会话中查找，减少大模型的工具地调用；如果历史会话中没有，那么就去工具中查询
""")


async def run_demo():
    """运行"""

    app_name = "assistant_demo"

    # 创建Agent和Runner
    agent = create_assistant_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    demo_queries = [
        "What's your name?",
        "what is the weather like in paris?",
        "Do you remember my name?",
    ]

    for query in demo_queries:
        print(f"📝 用户: {query}")

        history_record = make_user_history_record()
        history_content = history_record.build_content(query)
        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 助手: ", end="", flush=True)
        events = []
        # 设置是否保存在历史中
        run_config = RunConfig(save_history_enabled=True)
        async for event in runner.run_async(user_id=user_id,
                                            session_id=session_id,
                                            new_message=[history_content, user_content],
                                            run_config=run_config):
            # 检查event.content是否存在
            if not event.content or not event.content.parts:
                continue
            events.append(event)
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

        print("\n" + "-" * 40)


if __name__ == "__main__":
    print("-" * 40)
    print("-" * 40)
    asyncio.run(run_demo())
