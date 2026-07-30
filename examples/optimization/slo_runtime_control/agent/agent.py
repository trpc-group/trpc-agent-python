# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""客户工单分类 agent —— SLO Runtime Control example 专用。

每次 create_agent() 重读 prompts/system.md，使优化器写入的新候选立即生效。
单文件优化目标。
"""

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config


SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


def _create_model() -> LLMModel:
    """构建 OpenAI 兼容 chat 模型实例。"""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _read_instruction() -> str:
    """从磁盘重读 system.md。"""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def create_agent() -> LlmAgent:
    """构建一个使用当前磁盘 prompt 的新 LlmAgent 实例。"""
    return LlmAgent(
        name="ticket_classifier_agent",
        description="A customer-service ticket classifier under multi-stop SLO control.",
        model=_create_model(),
        instruction=_read_instruction(),
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=512,
        ),
    )
