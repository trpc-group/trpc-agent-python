# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Prompt definitions for generated graph workflow."""

LLMAGENT1_INSTRUCTION = """You are a query rewriting assistant. Rewrite the user's question into a clear, concise search query optimized for vector similarity search.

User question: {user_question}

Return a JSON object with:
- search_query: string
- keywords: string[]

Do not include any extra keys."""

LLMAGENT2_INSTRUCTION = """You are a helpful assistant. Based on the retrieved documents provided in the context, answer the user's question. If the documents don't contain relevant information, say so honestly.

Retrieved {doc_count} documents (top score: {score_1}):

--- Document 1 ---
{doc_1}

--- Document 2 ---
{doc_2}

--- Document 3 ---
{doc_3}

User's original question: {user_question}"""
