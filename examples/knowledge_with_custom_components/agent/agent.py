# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Knowledge agent module. """

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

from .config import EMBEDDER_MODEL_NAME, TEST_DATA_FILE
from .prompts import DOCUMENT_LOADER_PROMPT, TEXT_SPLITTER_PROMPT, RETRIEVER_PROMPT
from .tools import CustomDocumentLoader, CustomTextSplitter, ToyRetriever


def create_document_loader_knowledge() -> LangchainKnowledge:
    """Create a LangchainKnowledge with custom document loader."""
    prompt = ChatPromptTemplate.from_template(DOCUMENT_LOADER_PROMPT)
    embedder = HuggingFaceEmbeddings(model_name=EMBEDDER_MODEL_NAME)
    vectorstore = InMemoryVectorStore(embedder)
    text_loader = CustomDocumentLoader(TEST_DATA_FILE)
    # chunk_size is set to 10 because the test text is short; adjust according to text length in practice
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


def create_text_splitter_knowledge() -> LangchainKnowledge:
    """Create a LangchainKnowledge with custom text splitter."""
    prompt = ChatPromptTemplate.from_template(TEXT_SPLITTER_PROMPT)
    embedder = HuggingFaceEmbeddings(model_name=EMBEDDER_MODEL_NAME)
    vectorstore = InMemoryVectorStore(embedder)
    text_loader = TextLoader(TEST_DATA_FILE)
    text_splitter = CustomTextSplitter(separator="\n")

    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


def create_retriever_knowledge() -> LangchainKnowledge:
    """Create a LangchainKnowledge with custom retriever."""
    prompt = PromptTemplate.from_template(RETRIEVER_PROMPT)
    test_documents = [
        Document(page_content="Shenzhen: sunny", metadata={"source": "weather.txt"}),
        Document(page_content="Shanghai: cloud", metadata={"source": "weather.txt"})
    ]
    retriever = ToyRetriever.from_documents(test_documents, k=1)

    rag = LangchainKnowledge(
        prompt_template=prompt,
        retriever=retriever,
    )
    return rag
