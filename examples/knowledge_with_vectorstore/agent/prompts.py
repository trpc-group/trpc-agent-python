# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" prompts for agent"""

from langchain_core.prompts import ChatPromptTemplate

INSTRUCTION = "You are a helpful assistant. Be conversational and remember our previous exchanges."

RAG_PROMPT_TEMPLATE = """Answer the question gently:
    Query: {query}
    """

rag_prompt = ChatPromptTemplate.from_template(RAG_PROMPT_TEMPLATE)
