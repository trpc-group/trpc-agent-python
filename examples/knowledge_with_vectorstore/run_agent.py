# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
import asyncio
import uuid

from dotenv import load_dotenv

from trpc_agent.runners import Runner
from trpc_agent.sessions import InMemorySessionService
from trpc_agent.types import Content, Part

# Load environment variables from the .env file
load_dotenv()


async def run_rag_agent():
    """Run the RAG knowledge agent demo with vectorstore"""

    app_name = "rag_vectorstore_demo"

    from agent.agent import root_agent
    from agent.tools import rag, get_create_vectorstore_kwargs

    # 从文档创建向量数据库（如知识库已构建好，可跳过此步骤）
    await rag.create_vectorstore_from_document(**get_create_vectorstore_kwargs())

    # 执行对话，agent将使用search结果作为参考
    session_service = InMemorySessionService()
    user_id = "demo_user"
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    # 演示查询列表
    demo_queries = [
        "shenzhen weather",
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        # 为新session创建状态变量
        # 如果不需要管理会话，可以不需要用session_service，trpc_agent会自动创建会话
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
            # 检查event.content是否存在
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                # 跳过思考部分，partial=True时已经输出了
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [调用工具: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [工具结果: {part.function_response.response}]")
                # 取消注释，可以获得LLM完整的text输出
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_rag_agent())
