# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""共享的 SDK 业务 Agent：模型可注入，Prompt 每次重新读取。"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


ModelFactory = Callable[[], LLMModel]


def render_instruction(prompts: dict[str, str]) -> str:
    """稳定拼接一个或多个工作 Prompt 字段。"""
    if len(prompts) == 1:
        return next(iter(prompts.values()))
    return "\n\n".join(
        f"## {name}\n{content}" for name, content in prompts.items()
    )


class BusinessAgent:
    """以独立 SDK Session 执行一次 Prompt 敏感的业务请求。"""

    def __init__(
        self,
        target_prompt: TargetPrompt,
        model_factory: ModelFactory,
        *,
        agent_name: str,
        app_name: str,
        user_id: str,
    ) -> None:
        self._target_prompt = target_prompt
        self._model_factory = model_factory
        self._agent_name = agent_name
        self._app_name = app_name
        self._user_id = user_id

    async def call_agent(self, query: str) -> str:
        """重新读取 Prompt，运行独立 Session，并返回最终可见文本。"""
        if not isinstance(query, str):
            raise TypeError("query must be a string")

        prompts = await self._target_prompt.read_all()
        model = self._model_factory()
        root_agent = LlmAgent(
            name=self._agent_name,
            description="Evaluation and prompt optimization business agent.",
            model=model,
            instruction=render_instruction(prompts),
            generate_content_config=GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=512,
            ),
        )
        session_service = InMemorySessionService()
        runner = Runner(
            app_name=self._app_name,
            agent=root_agent,
            session_service=session_service,
        )
        session_id = str(uuid4())
        await session_service.create_session(
            app_name=self._app_name,
            user_id=self._user_id,
            session_id=session_id,
            state={},
        )
        message = Content(
            role="user",
            parts=[Part.from_text(text=query)],
        )
        final_text = ""
        async for event in runner.run_async(
            user_id=self._user_id,
            session_id=session_id,
            new_message=message,
        ):
            if (
                not event.is_final_response()
                or not event.content
                or not event.content.parts
            ):
                continue
            for part in event.content.parts:
                if not part.thought and part.text:
                    final_text += part.text
        return final_text.strip()
