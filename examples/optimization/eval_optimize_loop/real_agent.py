# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""真实业务模型适配器：每个评测 case 都读取当前工作 Prompt。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.models import OpenAIModel

from .business_agent import BusinessAgent


@dataclass(frozen=True)
class BusinessModelConfig:
    """来自环境变量的业务模型连接信息。"""

    api_key: str
    base_url: str
    model_name: str


def load_business_model_config(
    environ: Mapping[str, str] | None = None,
) -> BusinessModelConfig:
    """读取业务模型环境变量，缺失时一次性报告全部字段。"""
    values = os.environ if environ is None else environ
    names = (
        "TRPC_AGENT_API_KEY",
        "TRPC_AGENT_BASE_URL",
        "TRPC_AGENT_MODEL_NAME",
    )
    missing = [name for name in names if not values.get(name, "").strip()]
    if missing:
        raise ValueError(f"missing required environment variables: {', '.join(missing)}")
    return BusinessModelConfig(
        api_key=values["TRPC_AGENT_API_KEY"].strip(),
        base_url=values["TRPC_AGENT_BASE_URL"].strip(),
        model_name=values["TRPC_AGENT_MODEL_NAME"].strip(),
    )


class RealBusinessAgent:
    """以真实模型执行评测，并确保 case 与 Prompt 版本相互隔离。"""

    def __init__(self, target_prompt: TargetPrompt, config: BusinessModelConfig) -> None:
        self._delegate = BusinessAgent(
            target_prompt,
            lambda: OpenAIModel(
                model_name=config.model_name,
                api_key=config.api_key,
                base_url=config.base_url,
            ),
            agent_name="eval_optimize_real_agent",
            app_name="eval_optimize_real_integration",
            user_id="real-integration",
        )

    async def call_agent(self, query: str) -> str:
        """重新读取工作 Prompt，运行独立 session，只返回正式最终文本。"""
        return await self._delegate.call_agent(query)
