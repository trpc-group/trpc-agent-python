# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext

from ._constants import SKILL_CONFIG_KEY
from ._constants import SKILL_LOAD_MODE_VALUES
from ._constants import SkillLoadModeNames

DEFAULT_SKILL_CONFIG = {
    "skill_processor": {
        "load_mode": "turn",
        "tooling_guidance": "",
        "tool_result_mode": False,
        "tool_profile": "full",
        "forbidden_tools": [],
        "tool_flags": None,
        "exec_tools_disabled": False,
        "repo_resolver": None,
        "max_loaded_skills": 0,
    },
    "workspace_exec_processor": {
        "session_tools": False,
        "has_skills_repo": False,
        "repo_resolver": None,
        "enabled_resolver": None,
        "sessions_resolver": None,
    },
    "skills_tool_result_processor": {
        "skip_fallback_on_session_summary": True,
        "repo_resolver": None,
        "tool_result_mode": False,
    },
}


def get_skill_config(agent_context: AgentContext) -> dict[str, Any]:
    return agent_context.get_metadata(SKILL_CONFIG_KEY, DEFAULT_SKILL_CONFIG)


def set_skill_config(agent_context: AgentContext, config: dict[str, Any] = DEFAULT_SKILL_CONFIG) -> None:
    agent_context.with_metadata(SKILL_CONFIG_KEY, config)


def get_skill_load_mode(ctx: InvocationContext) -> str:
    skill_config = get_skill_config(ctx.agent_context)
    load_mode = skill_config["skill_processor"].get("load_mode", SkillLoadModeNames.TURN.value)
    if load_mode not in SKILL_LOAD_MODE_VALUES:
        load_mode = SkillLoadModeNames.TURN.value
    return str(load_mode)


def is_exist_skill_config(agent_context: AgentContext) -> bool:
    return SKILL_CONFIG_KEY in agent_context.metadata
