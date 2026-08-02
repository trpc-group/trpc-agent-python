# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Plan Mode — session-scoped design + approval gate before implementation.

Mount with :func:`setup_plan` on a :class:`trpc_agent_sdk.agents.LlmAgent`.
Pair with :class:`trpc_agent_sdk.tools.SpawnSubAgentTool` and built-in
``EXPLORE_AGENT`` / ``PLAN_AGENT`` archetypes for read-only exploration and design.
"""

from ._controller import ApprovalEvent
from ._long_running_tools import make_enter_plan_mode_tool
from ._helpers import DEFAULT_READONLY_SUBAGENT_TYPES
from ._helpers import DEFAULT_READONLY_TOOL_NAMES
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import DEFAULT_WRITE_TOOL_NAMES
from ._helpers import PLAN_TOOL_NAMES
from ._helpers import decode_plan
from ._helpers import encode_plan
from ._helpers import get_plan_record
from ._helpers import plan_to_task_subjects
from ._helpers import render_plan
from ._helpers import state_key
from ._models import PlanQuestion
from ._models import PlanRecord
from ._models import PlanStatus
from ._plan_toolset import PlanToolSet
from ._prompt import DEFAULT_PLAN_AWARENESS_PROMPT
from ._prompt import DEFAULT_PLAN_MODE_PROMPT
from ._setup import OnApproval
from ._setup import PlanOptions
from ._setup import setup_plan
from ._update_plan_content_tool import UpdatePlanContentTool

__all__ = [
    "ApprovalEvent",
    "make_enter_plan_mode_tool",
    "OnApproval",
    "PlanOptions",
    "PlanQuestion",
    "PlanRecord",
    "PlanStatus",
    "PlanToolSet",
    "UpdatePlanContentTool",
    "DEFAULT_PLAN_AWARENESS_PROMPT",
    "DEFAULT_PLAN_MODE_PROMPT",
    "DEFAULT_READONLY_SUBAGENT_TYPES",
    "DEFAULT_READONLY_TOOL_NAMES",
    "DEFAULT_STATE_KEY_PREFIX",
    "DEFAULT_WRITE_TOOL_NAMES",
    "PLAN_TOOL_NAMES",
    "decode_plan",
    "encode_plan",
    "get_plan_record",
    "plan_to_task_subjects",
    "render_plan",
    "setup_plan",
    "state_key",
]
