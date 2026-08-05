# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run configuration for TRPC Agent framework."""

from __future__ import annotations

import sys
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from trpc_agent_sdk.log import logger

from ._agent_run_limits import AgentRunLimits
from ._prompt_cache_config import PromptCacheConfig


class RunConfig(BaseModel):
    """Configs for runtime behavior of agents."""

    model_config = ConfigDict(extra="forbid", )
    """The pydantic model config."""

    max_llm_calls: int = 500
    """
    A limit on the total number of llm calls for each agent invocation.

    Valid Values:
      - More than 0 and less than sys.maxsize: The bound on the number of llm
        calls is enforced, if the value is set in this range.
      - Less than or equal to 0: This allows for unbounded number of llm calls.
    """

    max_iterations: int = Field(default=0, ge=0)
    """Maximum loop iterations for each agent invocation.

    A value of ``0`` disables this limit. The counter is local to each call to
    an agent's ``run_async`` method.
    """

    max_tool_calls: int = Field(default=0, ge=0)
    """Maximum tool calls for each agent invocation.

    A value of ``0`` disables this limit. A batch that would exceed the limit
    is rejected before any tool in that batch is executed.
    """

    agent_limits: dict[str, AgentRunLimits] = Field(default_factory=dict)
    """Per-agent limit overrides keyed by the exact ``agent.name``.

    Unset fields inherit their top-level values from this ``RunConfig``. A
    per-agent value of ``0`` explicitly disables the corresponding inherited
    limit for that agent.
    """

    streaming: bool = True
    """Whether to enable streaming mode. Default is True."""

    agent_run_config: dict[str, Any] = Field(default_factory=dict)
    """
    Additional config for the agent when invoke run_async.
    """

    custom_data: dict[str, Any] = Field(default_factory=dict)
    """
    Custom data that can be passed to model factory callbacks for dynamic model creation.

    This data is accessible in model factory callbacks to enable runtime configuration,
    such as dynamic API key retrieval or per-request model customization.
    """

    save_history_enabled: bool = False
    """ Save history enabled."""

    prompt_cache: Optional[PromptCacheConfig] = None
    """Per-run prompt cache configuration override.

    When set, this takes precedence over the model-level ``prompt_cache_config``
    for the duration of the run. When ``None``, the model-level config (if any)
    is used.
    """

    start_from_last_agent: bool = False
    """
    Whether to start from the last active agent in the session instead of the root agent.

    When True:
    - The runner will search session events to find the last responding agent
    - If a matching agent is found in the current agent tree, execution resumes from that agent
    - Falls back to root agent if no suitable agent is found

    When False (default):
    - Always start from the root agent for normal scenarios
    - This is the current default behavior

    Note: Human-in-the-loop scenarios always resume from the agent that triggered
    the long-running operation, regardless of this setting.
    """

    @field_validator("max_llm_calls", mode="after")
    @classmethod
    def validate_max_llm_calls(cls, value: int) -> int:
        if value == sys.maxsize:
            raise ValueError(f"max_llm_calls should be less than {sys.maxsize}.")
        elif value <= 0:
            logger.warning(
                "max_llm_calls is less than or equal to 0. This will result in"
                " no enforcement on total number of llm calls that will be made for a"
                " run. This may not be ideal, as this could result in a never"
                " ending communication between the model and the agent in certain"
                " cases.", )

        return value
