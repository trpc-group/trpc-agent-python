# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Knowledge tools module for TRPC Agent framework."""

from .langchain_knowledge_searchtool import AgenticLangchainKnowledgeSearchTool
from .langchain_knowledge_searchtool import LangchainKnowledgeSearchTool

__all__ = [
    "AgenticLangchainKnowledgeSearchTool",
    "LangchainKnowledgeSearchTool",
]
