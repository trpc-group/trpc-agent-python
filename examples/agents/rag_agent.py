# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import tempfile
import uuid

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
try:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge


def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = InMemoryVectorStore(embedder)
    # 使用 TextLoader：将文本写入临时文件后加载
    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
    tmp_file.write(text_content)
    tmp_file.flush()
    tmp_file.close()
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # text_loader = TextLoader("/trpc-agent/examples/agents/rag_test.txt", encoding="utf-8")
    # 这里由于测试文本较短，所以chunk_size设置为10，实际使用时需要根据文本长度调整
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


rag = build_chain()


async def simple_search(query: str):
    # metadata 可用于存储元数据
    metadata = {
        'assistant_name': 'test',  # Agent Name, 可用于上下文
        'runnable_config': {},  # Langchain中的Runnable配置
    }
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}


model = OpenAIModel(
    model_name="deepseek-v3-local-II",
    api_key=os.environ.get("API_KEY", ""),
    base_url="http://v2.open.venus.woa.com/llmproxy",
)

root_agent = LlmAgent(
    name="rag_agent",
    description="A helpful assistant for conversation, ",
    model=model,  # You can change this to your preferred model
    instruction="You are a helpful assistant. Be conversational and remember our previous exchanges.",
    tools=[FunctionTool(simple_search)],
)


async def run_rag_demo(query: str):
    # 从文档创建向量数据库
    await rag.create_vectorstore_from_document()

    # 执行对话，agent将使用search结果作为参考
    session_service = InMemorySessionService()
    app_name = "rag_demo"
    user_id = "demo_user"
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
    user_content = Content(parts=[Part.from_text(text=query)])
    current_session_id = str(uuid.uuid4())

    # 为新session创建状态变量
    # 如果不需要管理会话，可以不需要用session_service，trpc_agent会自动创建会话
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=current_session_id,
    )

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


if __name__ == "__main__":
    print("Input your question:")
    # query = sys.stdin.readline().strip()
    query = "什么是人工智能?"
    asyncio.run(run_rag_demo(query))
