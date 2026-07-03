# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Constants and isolation defaults for the dynamic sub-agent subsystem."""

from __future__ import annotations

SUBAGENT_APP_NAME_SUFFIX = "_trpc_subagent_"
SUBAGENT_USER_ID = "subagent_user"

# LlmAgent fields that must be flattened on every sub-agent so callbacks /
# transfer hints / output sinks from the parent never leak in. Adding a new
# archetype does not require remembering which fields to wipe — they live here.
ISOLATION_DEFAULTS: dict = {
    "sub_agents": [],
    "parent_agent": None,
    "default_transfer_message": "",
    "output_schema": None,
    "input_schema": None,
    "output_key": None,
    "before_agent_callback": None,
    "after_agent_callback": None,
    "before_model_callback": None,
    "after_model_callback": None,
    "before_tool_callback": None,
    "after_tool_callback": None,
}
