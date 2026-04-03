# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools for the skill run agent with dynamic tool loading support."""

from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import DynamicSkillToolSet
from trpc_agent_sdk.tools import FunctionTool

from ._tools import ask_name_information


def create_skill_dynamic_tool_set(skill_repository: BaseSkillRepository, only_active_skills: bool = True):
    """Create skill dynamic tool set."""
    available_tools = {
        "search_city_by_name",
        "get_weather_forecast",
        "search_city_by_name",
        FunctionTool(ask_name_information),
    }
    return DynamicSkillToolSet(skill_repository=skill_repository,
                               available_tools=available_tools,
                               only_active_skills=only_active_skills)
