# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Construction-time defaults applied to every spawned sub-agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.types import GenerateContentConfig


@dataclass(frozen=True)
class SubAgentConfig:
    """Configuration for every spawned sub-agent.

    ``None`` means "inherit from the parent agent or use the default".
    """

    model: Optional[LLMModel] = None
    """Model the sub-agent uses.  ``None`` inherits the parent's model."""

    generate_content_config: Optional[GenerateContentConfig] = None
    """Generation configuration (temperature, top_p, etc.).  ``None`` inherits from parent."""

    parallel_tool_calls: Optional[bool] = None
    """Whether the sub-agent may issue parallel tool calls.  ``None`` inherits from parent."""

    include_parent_history: bool = False
    """Whether to inject parent conversation history into the sub-agent's session."""

    max_parent_history_turns: Optional[int] = None
    """Max parent turns to inject.  ``None`` = unlimited.
    Only used when ``include_parent_history`` is ``True``."""

    max_turns: Optional[int] = None
    """Max LLM calls the sub-agent may make.  ``None`` = unlimited.
    Each LLM request counts as one turn, including those with tool calls."""

    forward_events: bool = False
    """Stream sub-agent events to the parent consumer as progress updates.

    ``True``: orchestrator can display sub-agent execution live.
    ``False`` (default): sub-agent runs silently."""


__all__ = ["SubAgentConfig"]
