# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
import asyncio
import os
import sys
import uuid

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

connection = "postgresql+psycopg://langchain:langchain@X.X.X.X:X/langchain"  # Uses psycopg3!
collection_name = "my_docs"


def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = PGVector(
        embeddings=embedder,
        collection_name=collection_name,
        connection=connection,
        use_jsonb=True,
    )
    # 使用相对路径，避免硬编码绝对路径
    text_loader = TextLoader("/trpc-agent/examples/agents/pgvector_rag_test.txt", encoding="utf-8")
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


async def run_rag_demo(query: str, need_create_vdb: bool = False):
    if need_create_vdb:
        # 演示知识库不存在时，使用LangchainKnowledge的create_vectorstore_from_document方法创建向量数据库
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
    query = sys.stdin.readline().strip()
    print(f"query: {query}")
    asyncio.run(run_rag_demo(query, True))
