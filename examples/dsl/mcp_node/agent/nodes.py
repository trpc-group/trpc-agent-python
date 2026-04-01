# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_LLMAGENT1 = 'planner_agent'
NODE_ID_TRANSFORM1 = 'prepare_request'
NODE_ID_MCP1 = 'mcp_weather'
NODE_ID_TRANSFORM2 = 'format_summary'
NODE_ID_END1 = 'end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_transform1(state: WorkflowState) -> dict[str, Any]:
    value = {
        "a": state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['a'],
        "b": state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['b']
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM1: value}}


async def node_transform2(state: WorkflowState) -> dict[str, Any]:
    value = {
        "summary": "Addition result is " + str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_MCP1]),
        "raw": state[STATE_KEY_NODE_RESPONSES][NODE_ID_MCP1]
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM2: value}}


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    value = {
        "message": state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM2]['summary'],
        "result": state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM2]['raw']
    }
    last_response = value

    return {
        STATE_KEY_NODE_RESPONSES: {
            NODE_ID_END1: value
        },
        STATE_KEY_LAST_RESPONSE: last_response,
    }
