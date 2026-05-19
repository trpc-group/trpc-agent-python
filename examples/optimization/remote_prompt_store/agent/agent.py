# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""数学题求解 agent —— Remote Prompt Store example 专用。

与 quickstart / http_service 的关键差异
---------------------------------------
本 agent **不读 prompt 文件**——prompt 通过 create_agent(prompt_text) 的
入参传入。call_agent 在每次调用时先从远端 KV 拉最新 prompt，再用它
构造 agent 实例。

这种"prompt 通过参数注入"的形态是远端 KV 场景的自然写法：业务服务
在每次请求时从配置中心拉 prompt，再创建 agent，不依赖任何本地文件。
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config


def _create_model() -> LLMModel:
    """构建 OpenAI 兼容 chat 模型实例。凭据从环境变量读取。"""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def create_agent(prompt_text: str) -> LlmAgent:
    """用给定 prompt 文本构造一个 LlmAgent 实例。

    参数 prompt_text 由调用方（call_agent）从远端 KV 现读现传，
    所以优化器把候选写入 KV 后下一次调用立即生效。
    """
    return LlmAgent(
        name="math_word_problem_agent",
        description="Math word-problem solver whose prompt lives in a remote KV store.",
        model=_create_model(),
        instruction=prompt_text,
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=2048,
        ),
    )
