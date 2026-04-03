# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()

MAX_RETRIES = 3


async def _create_vectorstore_with_retry(rag, retries: int = MAX_RETRIES) -> bool:
    """Attempt to create vector store from document with retries."""
    for attempt in range(1, retries + 1):
        try:
            await rag.create_vectorstore_from_document()
            logger.info("向量数据库创建成功")
            return True
        except FileNotFoundError:
            logger.error("文档文件不存在，请检查文件路径配置", exc_info=True)
            break
        except ValueError as exc:
            logger.error("参数配置错误: %s", exc, exc_info=True)
            break
        except Exception:
            logger.error(
                "向量数据库创建失败 (第 %d/%d 次尝试)",
                attempt,
                retries,
                exc_info=True,
            )
            if attempt < retries:
                wait = 2**attempt
                logger.info("将在 %d 秒后重试...", wait)
                await asyncio.sleep(wait)

    logger.error("向量数据库创建最终失败，已达最大重试次数 (%d)", retries)
    return False


async def run_single_demo(prompt_template_name: str, description: str, query: str) -> None:
    """运行单个 Prompt Template 示例"""
    from agent.agent import create_agent
    from agent.tools import build_search_tool

    print(f"\n{'=' * 60}")
    print(f"📋 示例：{description}")
    print(f"   Prompt Template 类型：{prompt_template_name}")
    print(f"{'=' * 60}")

    rag, search_tool = build_search_tool(prompt_template_name)

    if not await _create_vectorstore_with_retry(rag):
        logger.error("跳过示例 [%s]：向量数据库创建失败", prompt_template_name)
        return

    agent = create_agent(search_tool, name=f"rag_agent_{prompt_template_name}")

    app_name = f"prompt_template_demo_{prompt_template_name}"
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

    user_id = "demo_user"
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
                print(f"\n🔧 [调用工具: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [工具结果: {part.function_response.response}]")

    print("\n" + "-" * 40)


async def run_all_demos() -> None:
    """依次运行三种 Prompt Template 示例"""

    demos = [
        {
            "prompt_template_name": "string_prompt",
            "description": "PromptTemplate（StringPromptTemplate）— 格式化单个字符串",
            "query": "什么是人工智能？",
        },
        {
            "prompt_template_name": "chat_prompt",
            "description": "ChatPromptTemplate — 格式化消息列表",
            "query": "深度学习和机器学习有什么关系？",
        },
        {
            "prompt_template_name": "messages_prompt",
            "description": "MessagesPlaceholder — 支持对话历史的消息模版",
            "query": "人工智能有哪些研究领域？",
        },
    ]

    print("🚀 Knowledge with Prompt Template 示例")
    print("   本示例展示三种 Prompt Template 在 RAG 知识库中的用法\n")

    for demo in demos:
        await run_single_demo(**demo)

    print(f"\n{'=' * 60}")
    print("✅ 所有示例运行完成")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(run_all_demos())
