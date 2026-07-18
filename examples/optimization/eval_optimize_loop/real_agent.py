# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""真实业务模型适配器：每个评测 case 都读取当前工作 Prompt。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Mapping

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


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


def _render_instruction(prompts: dict[str, str]) -> str:
    """稳定拼接一个或多个工作 Prompt 字段。"""
    if len(prompts) == 1:
        return next(iter(prompts.values()))
    return "\n\n".join(f"## {name}\n{content}" for name, content in prompts.items())


class RealBusinessAgent:
    """以真实模型执行评测，并确保 case 与 Prompt 版本相互隔离。"""

    def __init__(self, target_prompt: TargetPrompt, config: BusinessModelConfig) -> None:
        self._target_prompt = target_prompt
        self._config = config

    async def call_agent(self, query: str) -> str:
        """重新读取工作 Prompt，运行独立 session，只返回正式最终文本。"""
        prompts = await self._target_prompt.read_all()
        model = OpenAIModel(
            model_name=self._config.model_name,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
        )
        root_agent = LlmAgent(
            name="eval_optimize_real_agent",
            description="真实模型驱动的评测与 Prompt 优化示例 Agent。",
            model=model,
            instruction=_render_instruction(prompts),
            generate_content_config=GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=512,
            ),
        )
        session_service = InMemorySessionService()
        runner = Runner(
            app_name="eval_optimize_real_integration",
            agent=root_agent,
            session_service=session_service,
        )
        session_id = str(uuid.uuid4())
        user_id = "real-integration"
        await session_service.create_session(
            app_name="eval_optimize_real_integration",
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        message = Content(role="user", parts=[Part.from_text(text=query)])
        final_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            if not event.is_final_response() or not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if not part.thought and part.text:
                    final_text += part.text
        return final_text.strip()
