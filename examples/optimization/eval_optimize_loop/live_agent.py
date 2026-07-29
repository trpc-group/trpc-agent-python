#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Real model-backed call_agent used by native AgentOptimizer mode."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, GenerateContentConfig, Part

CallAgent = Callable[[str], Awaitable[str]]
APP_NAME = "eval_optimize_loop"


def model_info_from_env() -> dict[str, Any]:
    """Return non-secret model identity for the frozen manifest."""

    return {
        "provider": os.environ.get("TRPC_AGENT_PROVIDER_NAME", "openai"),
        "model_name": os.environ.get("TRPC_AGENT_MODEL_NAME", ""),
        # Do not persist an endpoint that might embed credentials.
        "base_url_configured": bool(os.environ.get("TRPC_AGENT_BASE_URL")),
        "usage_tracking": False,
    }


def validate_model_env() -> None:
    missing = [
        key for key in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME") if not os.environ.get(key)
    ]
    if missing:
        raise RuntimeError(
            "native optimize 需要模型配置："
            + ", ".join(f"${key}" for key in missing)
            + "；无需 API Key 的回归演示请使用 --mode fake。"
        )


def build_call_agent(prompt_path: Path) -> CallAgent:
    """Build a callback that reloads the current TargetPrompt on every call."""

    validate_model_env()
    api_key = os.environ["TRPC_AGENT_API_KEY"]
    base_url = os.environ["TRPC_AGENT_BASE_URL"]
    model_name = os.environ["TRPC_AGENT_MODEL_NAME"]

    async def call_agent(query: str) -> str:
        instruction = prompt_path.read_text(encoding="utf-8")
        model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)
        agent = LlmAgent(
            name="eval_optimize_loop_agent",
            description="Agent evaluated and optimized by Issue #91 pipeline.",
            model=model,
            instruction=instruction,
            generate_content_config=GenerateContentConfig(
                temperature=0.2,
                top_p=0.9,
                max_output_tokens=2048,
            ),
        )
        sessions = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
        session_id = str(uuid.uuid4())
        user_id = "optimizer"
        await sessions.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        message = Content(role="user", parts=[Part.from_text(text=query)])
        response = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            if not event.is_final_response() or not event.content:
                continue
            for part in event.content.parts or []:
                if not part.thought and part.text:
                    response += part.text
        return response.strip()

    # The SDK runner does not expose a provider-independent USD amount here.
    # Treat the whole optimize run as unmeasured so G6 blocks auto-apply.
    call_agent.cost_status = "unavailable"
    call_agent.total_tokens = None
    call_agent.total_cost = None
    return call_agent
