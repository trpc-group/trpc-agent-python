# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Per-agent run-limit configuration."""

from __future__ import annotations

import sys
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class AgentRunLimits(BaseModel):
    """Per-agent overrides for limits configured on :class:`RunConfig`.

    A field set to ``None`` inherits the corresponding top-level value from
    :class:`RunConfig`. A value of ``0`` disables that limit for the selected
    agent, while a positive value enables it.
    """

    model_config = ConfigDict(extra="forbid")
    """The Pydantic model configuration."""

    max_llm_calls: Optional[int] = Field(default=None, ge=0, lt=sys.maxsize)
    """Maximum logical LLM calls for each invocation of the selected agent."""

    max_iterations: Optional[int] = Field(default=None, ge=0)
    """Maximum loop iterations for each invocation of the selected agent."""

    max_tool_calls: Optional[int] = Field(default=None, ge=0)
    """Maximum tool calls for each invocation of the selected agent."""
