# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Prompts for knowledge components. """

DOCUMENT_LOADER_PROMPT = """Answer the question gently:
    Query: {query}
    """

TEXT_SPLITTER_PROMPT = """Answer the question gently:
    Query: {query}
    """

RETRIEVER_PROMPT = "{query}"
