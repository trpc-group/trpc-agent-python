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


async def run_documentloader_agent() -> None:
    """Run the DocumentLoader knowledge agent demo"""

    app_name = "documentloader_agent_demo"

    from agent.agent import root_agent
    from agent.tools import rag

    if not await _create_vectorstore_with_retry(rag):
        logger.error("无法创建向量数据库，程序退出")
        return

    # 执行对话，agent将使用search结果作为参考
    session_service = InMemorySessionService()
    user_id = "demo_user"
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    # 演示查询列表
    demo_queries = [
        "什么是人工智能?",
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
    asyncio.run(run_documentloader_agent())
