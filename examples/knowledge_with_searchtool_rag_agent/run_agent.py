# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def run_rag_agent():
    """Run the RAG knowledge agent demo with LangchainKnowledgeSearchTool"""

    app_name = "searchtool_rag_agent_demo"

    from agent.agent import root_agent
    from agent.tools import rag

    # 从文档创建向量数据库
    await rag.create_vectorstore_from_document()

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # 演示查询列表
    demo_queries = [
        "什么是人工智能?",
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
        )

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_rag_agent())
