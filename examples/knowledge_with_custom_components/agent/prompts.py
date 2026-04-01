# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Prompts for knowledge components. """

DOCUMENT_LOADER_PROMPT = """Answer the question gently:
    Query: {query}
    """

TEXT_SPLITTER_PROMPT = """Answer the question gently:
    Query: {query}
    """

RETRIEVER_PROMPT = "{query}"
