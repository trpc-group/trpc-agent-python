# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" prompts for agent"""

from langchain_core.prompts import ChatPromptTemplate

INSTRUCTION = "You are a helpful assistant. Be conversational and remember our previous exchanges."

RAG_PROMPT_TEMPLATE = """Answer the question gently:
    Query: {query}
    """

rag_prompt = ChatPromptTemplate.from_template(RAG_PROMPT_TEMPLATE)
