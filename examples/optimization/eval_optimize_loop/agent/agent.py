# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Live agent bridge for the eval_optimize_loop example.

The optimizer contract is intentionally small: ``call_agent`` is an async
function that accepts one user query and returns the final response text. This
module re-reads the prompt file on every invocation so prompt candidates written
by AgentOptimizer take effect immediately.

The public bridge in this file mirrors the SDK docs:

* ``create_agent`` builds a fresh ``LlmAgent`` from the current prompt file.
* ``run_agent`` drives that agent through ``Runner`` and ``InMemorySessionService``.
* ``make_call_agent`` returns the exact async callable required by
  ``AgentOptimizer.optimize`` when a ``TargetPrompt`` is registered.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


APP_NAME = "eval_optimize_loop"


def lookup_order(order_id: str) -> str:
    """FunctionTool body used by the live ``LlmAgent`` example."""
    data = {
        "A100": "Order A100 is in transit and arrives on Friday.",
        "A200": "Order A200 is delivered.",
    }
    return data.get(order_id, f"No order record found for {order_id}.")


def search_policy(topic: str) -> str:
    """FunctionTool body for policy and warranty lookup examples."""
    topic_lower = topic.lower()
    if "damaged" in topic_lower or "refund" in topic_lower:
        return "Damaged items are eligible for a full refund within 30 days."
    if "model z" in topic_lower or "warranty" in topic_lower:
        return "Model Z has a 24-month warranty."
    return "No matching policy snippet was found."


def get_model_config() -> tuple[str, str, str]:
    """Read live model credentials consumed by ``OpenAIModel``."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise ValueError(
            "Live mode requires TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, and "
            "TRPC_AGENT_MODEL_NAME. Use --mode fake for the no-key path."
        )
    return api_key, base_url, model_name


def create_agent(prompt_path: Path) -> LlmAgent:
    """Create a fresh ``LlmAgent`` from the current prompt file.

    Re-reading here is the critical TargetPrompt contract: when
    ``AgentOptimizer`` writes a candidate prompt, the next call immediately uses
    that candidate without restarting the process.
    """
    api_key, base_url, model_name = get_model_config()
    instruction = Path(prompt_path).read_text(encoding="utf-8").strip()
    return LlmAgent(
        name="support_assistant",
        description="A support assistant whose system prompt is under optimization.",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url),
        instruction=instruction,
        tools=[FunctionTool(lookup_order), FunctionTool(search_policy)],
    )


async def run_agent(query: str, prompt_path: Path) -> dict[str, Any]:
    """Run the live agent once and collect final text plus tool calls.

    ``AgentOptimizer.optimize`` only needs final response text, but the outer
    issue-level report also wants key trajectory information. This richer helper
    supports both.
    """
    agent = create_agent(prompt_path)
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "optimizer"
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={},
    )
    message = Content(role="user", parts=[Part.from_text(text=query)])
    final_text = ""
    tools: list[dict[str, Any]] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=message,
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                tools.append(
                    {
                        "name": getattr(function_call, "name", None),
                        "args": dict(getattr(function_call, "args", {}) or {}),
                    }
                )
        if event.is_final_response():
            for part in event.content.parts:
                if getattr(part, "text", None) and not getattr(part, "thought", False):
                    final_text += part.text
    return {"text": final_text.strip(), "tools": tools}


def make_call_agent(prompt_path: Path) -> Callable[[str], Awaitable[str]]:
    """Return the fixed async ``(query: str) -> str`` bridge required by GEPA."""

    async def call_agent(query: str) -> str:
        return (await run_agent(query=query, prompt_path=prompt_path))["text"]

    return call_agent
