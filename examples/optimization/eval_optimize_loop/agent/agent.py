# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Online agent factory for the eval/optimization loop example."""

from __future__ import annotations

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "system.md"
ROUTER_PROMPT_PATH = PROMPT_DIR / "router.md"


def create_agent() -> LlmAgent:
    """Create an LlmAgent that re-reads prompt files on every call."""

    api_key, base_url, model_name = get_model_config()
    instruction = "\n\n".join(
        [
            SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip(),
            ROUTER_PROMPT_PATH.read_text(encoding="utf-8").strip(),
        ]
    )
    return LlmAgent(
        name="support_router_agent",
        model=OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        instruction=instruction,
    )
