# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent with knowledge_search for llm_rubric_knowledge_recall evaluator demo."""

from typing import Any
from typing import Dict

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def _create_model() -> OpenAIModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def knowledge_search(query: str, top_k: int = 3) -> Dict[str, Any]:
    """知识检索：根据 query 返回模拟的检索结果（示例用）。"""
    # 模拟检索结果，裁判将根据这些内容与 rubrics 判定召回质量
    mock_docs = [
        {
            "title": "产品A",
            "content": "产品A 适用于企业协作，支持文档与任务管理。"
        },
        {
            "title": "产品B",
            "content": "产品B 提供 API 与 SDK，便于集成。"
        },
        {
            "title": "产品C",
            "content": "产品C 面向个人用户，提供笔记与待办。"
        },
    ]
    return {
        "query": query,
        "results": mock_docs[:top_k],
    }


def create_agent() -> LlmAgent:
    """Create the agent for llm_rubric_knowledge_recall demo."""
    return LlmAgent(
        name="llm_rubric_knowledge_recall_agent",
        description="带知识检索的问答助手",
        model=_create_model(),
        instruction=("你是知识问答助手。用户提问时先调用 knowledge_search 检索相关知识，"
                     "再根据检索结果组织回答。必须调用 knowledge_search 后再回答。"),
        tools=[FunctionTool(knowledge_search)],
    )


root_agent = create_agent()
